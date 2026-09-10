#!/usr/bin/env python3
"""A07/A09 refusal regression tests (#84 / HARNESS-006).

Converted from probes in probe_defects.py (PR #89), which reproduced both
defects pre-fix on 2026-09-09:

  A07 — `verify --verifier <python> -c pass` accepted a verifier that was
        never init-pinned and bad source was then accepted. Now refused as
        verifier_changed_since_init (the runner's B3 pattern, ported).
  A09 — an unreadable gate-failed-open.log read as "no gaps" and acceptance
        proceeded without the expected evidence. Now: missing log is clean
        (the shims create it on the first gap), present-but-unreadable and
        torn lines refuse.

Each case carries its positive control so a refusal can never mean the probe
never ran.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ZCODE = Path(__file__).resolve().parents[1]
ZPROCTOR = ZCODE / "zproctor.py"

BUGGY = 'def sum_to_n(n):\n    return sum(range(1, n))\n'
FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15, f"sum_to_n(5)={sum_to_n(5)}, expected 15"\n'
    'print("PASS")\n'
)


def run_zp(args, state_root, timeout=120):
    env = dict(os.environ, ZPROCTOR_STATE_ROOT=str(state_root))
    r = subprocess.run([sys.executable, str(ZPROCTOR), *args],
                       capture_output=True, text=True, env=env, timeout=timeout)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


class A07VerifierSubstitution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zproctor-a07-")
        self.state = Path(self.tmp) / "state"
        self.ws = Path(self.tmp) / "ws"
        self.ws.mkdir(parents=True)
        (self.ws / "math_utils.py").write_text(BUGGY, encoding="utf-8")
        (self.ws / "check.py").write_text(CHECK, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _lane_init(self):
        rc, out = run_zp(["lane", "--task", "t", "--workspace", str(self.ws),
                          "--multi-module"], self.state)
        self.assertEqual(rc, 0, out)
        rc, out = run_zp(["init", "--task", "t", "--workspace", str(self.ws),
                          "--scope", "math_utils.py",
                          "--verifier", sys.executable, "check.py"], self.state)
        self.assertEqual(rc, 0, out)

    def test_substituted_verifier_refused_bad_source_not_accepted(self):
        self._lane_init()
        # Control: the init-pinned verifier fails on the buggy source.
        rc, out = run_zp(["verify", "--task", "t", "--workspace", str(self.ws)],
                         self.state)
        self.assertFalse(out.get("passed"))
        # The substitution attempt is refused, not honoured.
        rc, out = run_zp(["verify", "--task", "t", "--workspace", str(self.ws),
                          "--verifier", sys.executable, "-c", "pass"], self.state)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out.get("error"), "verifier_changed_since_init")
        self.assertEqual(out.get("pinned_argv"), [sys.executable, "check.py"])
        # And bad source is never accepted on the strength of a no-op exam.
        rc, out = run_zp(["accept", "--task", "t", "--workspace", str(self.ws)],
                         self.state)
        self.assertNotEqual(rc, 0, out)

    def test_pinned_verifier_still_works_on_fixed_source(self):
        self._lane_init()
        (self.ws / "math_utils.py").write_text(FIXED, encoding="utf-8")
        rc, out = run_zp(["verify", "--task", "t", "--workspace", str(self.ws)],
                         self.state)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.get("passed"))
        rc, out = run_zp(["accept", "--task", "t", "--workspace", str(self.ws)],
                         self.state)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.get("ok"))


class A09FailOpenEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zproctor-a09-")
        self.state = Path(self.tmp) / "state"
        self.ws = Path(self.tmp) / "ws"
        self.ws.mkdir(parents=True)
        (self.ws / "math_utils.py").write_text(FIXED, encoding="utf-8")
        (self.ws / "check.py").write_text(CHECK, encoding="utf-8")
        rc, out = run_zp(["lane", "--task", "t", "--workspace", str(self.ws),
                          "--multi-module"], self.state)
        self.assertEqual(rc, 0, out)
        rc, out = run_zp(["init", "--task", "t", "--workspace", str(self.ws),
                          "--scope", "math_utils.py",
                          "--verifier", sys.executable, "check.py"], self.state)
        self.assertEqual(rc, 0, out)
        rc, out = run_zp(["verify", "--task", "t", "--workspace", str(self.ws)],
                         self.state)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.get("passed"))
        self.fail_log = self.state / "gate-failed-open.log"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_written_gap_still_blocks_accept(self):
        # Control, unchanged from the probe: a real fail-open record refuses.
        self.fail_log.parent.mkdir(parents=True, exist_ok=True)
        self.fail_log.write_text(f"{int(time.time() * 1000) + 1}\tgap\n",
                                 encoding="utf-8")
        rc, out = run_zp(["accept", "--task", "t", "--workspace", str(self.ws)],
                         self.state)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out.get("error"), "gate_failed_open")

    def test_unreadable_log_refuses_instead_of_reading_clean(self):
        # The A09 defect: a directory where the log should be used to read as
        # "no gaps" and accept succeeded. Now it is UNKNOWN evidence.
        if self.fail_log.exists():
            self.fail_log.unlink()
        self.fail_log.mkdir()
        rc, out = run_zp(["accept", "--task", "t", "--workspace", str(self.ws)],
                         self.state)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out.get("error"), "gate_evidence_unreadable")

    def test_torn_line_refuses_instead_of_dropping_silently(self):
        if self.fail_log.exists():
            self.fail_log.unlink()
        if self.fail_log.is_dir():
            self.fail_log.rmdir()
        self.fail_log.parent.mkdir(parents=True, exist_ok=True)
        self.fail_log.write_text("not-a-timestamp-at-all\n", encoding="utf-8")
        rc, out = run_zp(["accept", "--task", "t", "--workspace", str(self.ws)],
                         self.state)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out.get("error"), "gate_evidence_malformed")

    def test_missing_log_still_means_no_gaps(self):
        # Missing is the normal clean state: the shims create the file only
        # on the first gap. This must keep accepting, or every fresh task
        # would wedge.
        if self.fail_log.exists():
            self.fail_log.unlink()
        rc, out = run_zp(["accept", "--task", "t", "--workspace", str(self.ws)],
                         self.state)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.get("ok"))


if __name__ == "__main__":
    unittest.main()
