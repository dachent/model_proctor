#!/usr/bin/env python3
"""The acceptance gate must consume what verify records (#36).

cmd_accept was three checks — receipt exists, passed, tree_sig matches — so
every other signal cmd_verify produced was discarded:

  * tamper_detected had no consumer anywhere (not accept, not record, not
    status), which made TOOL-014's restore-and-flag design decorative at the
    only point where it matters;
  * cmd_dispatch left a green receipt intact, so verify -> dispatch -> accept
    was gated by tree_sig alone. When the second dispatch changes no bytes the
    signature does not move and the FIRST dispatch's receipt authorises the
    accept — issue #17's sticky-flag defect, reproduced in the tool built to
    design it out.

Run: python -m unittest discover -s runner/tests -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "runner" / "runner.py"
FAKE_WORKER = ROOT / "runner" / "tests" / "fake_worker.py"

BUGGY = 'def sum_to_n(n):\n    return sum(range(1, n))\n'
FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15, f"sum_to_n(5)={sum_to_n(5)}, expected 15"\n'
    'print("PASS")\n'
)
# A verifier that passes unconditionally — what a worker would install in
# place of the sealed check.
NEUTERED_CHECK = 'print("PASS")\n'


def run_runner(*argv, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=120)
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip() else {})


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-gate-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_task(self):
        task = {
            "task_id": "t1",
            "prompt": "Fix the bug.",
            "features": {"bounded": True, "known_location": True,
                         "objective_acceptance": True},
            "scope": ["math_utils.py"],
            "verifier": {"argv": ["{python}", "check.py"]},
            "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60},
        }
        p = Path(self.tmp) / "task.json"
        p.write_text(json.dumps(task), encoding="utf-8")
        return str(p)

    def make_ws(self):
        ws = Path(self.tmp) / "ws"
        ws.mkdir()
        (ws / "math_utils.py").write_text(BUGGY, encoding="utf-8")
        (ws / "check.py").write_text(CHECK, encoding="utf-8")
        return str(ws)

    def dispatch_fix(self, ws, task, content=FIXED):
        return run_runner("dispatch", "--workspace", ws, "--task", task,
                          "--delegate", str(FAKE_WORKER),
                          env_extra={"FAKE_WORKER_WRITE": "math_utils.py",
                                     "FAKE_WORKER_CONTENT": content})


class TamperConsumptionTest(_Base):
    """A receipt flagged tamper_detected is not acceptable evidence."""

    def test_accept_refuses_tampered_receipt(self):
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        # Worker fixes the code AND neuters the sealed verifier.
        self.dispatch_fix(ws, task)
        (Path(ws) / "check.py").write_text(NEUTERED_CHECK, encoding="utf-8")

        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertTrue(ver["tamper_detected"], ver)
        self.assertEqual(ver["verifier_restored"], ["check.py"])
        self.assertTrue(ver["passed"], "restored check passes on fixed code")

        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1, f"accepted a tampered receipt: {out}")
        self.assertIn("tamper_detected", out.get("reason", ""))

    def test_refusal_is_recoverable_by_reverify(self):
        """The restore already made the files match the seal, so a second
        verify produces a clean receipt. The refusal must not deadlock."""
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch_fix(ws, task)
        (Path(ws) / "check.py").write_text(NEUTERED_CHECK, encoding="utf-8")
        run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(
            run_runner("accept", "--workspace", ws, "--task", task)[0], 1)

        rc, ver2 = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertFalse(ver2["tamper_detected"], ver2)
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["accepted"])

    def test_record_carries_tamper_flag(self):
        """The flag must survive into the durable evidence record."""
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch_fix(ws, task)
        (Path(ws) / "check.py").write_text(NEUTERED_CHECK, encoding="utf-8")
        run_runner("verify", "--workspace", ws, "--task", task)
        rc, out = run_runner("record", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["row"]["tamper_detected"], out["row"])

    def test_clean_run_records_false_not_none(self):
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch_fix(ws, task)
        run_runner("verify", "--workspace", ws, "--task", task)
        rc, out = run_runner("record", "--workspace", ws, "--task", task)
        self.assertIs(out["row"]["tamper_detected"], False)


class ReceiptFreshnessTest(_Base):
    """A dispatch after a green verify must invalidate the receipt."""

    def test_dispatch_after_green_verify_blocks_accept(self):
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch_fix(ws, task)
        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertTrue(ver.get("passed"), ver)
        self.assertEqual(ver["dispatch_seq"], 1)

        # The second dispatch writes identical content, so the tree signature
        # is unchanged on either code path — only the dispatch count moves.
        # This is the case tree_sig alone can never catch.
        self.dispatch_fix(ws, task)
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1, f"accepted across a dispatch boundary: {out}")
        self.assertIn("dispatches occurred after verification",
                      out.get("reason", ""))
        self.assertEqual(out["receipt_dispatch_seq"], 1)
        self.assertEqual(out["current_dispatches"], 2)

    def test_reverify_after_dispatch_restores_acceptability(self):
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch_fix(ws, task)
        run_runner("verify", "--workspace", ws, "--task", task)
        self.dispatch_fix(ws, task)
        self.assertEqual(
            run_runner("accept", "--workspace", ws, "--task", task)[0], 1)

        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(ver["dispatch_seq"], 2)
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["accepted"])

    def test_single_dispatch_path_unaffected(self):
        """Regression guard: the ordinary flow must not gain a refusal."""
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch_fix(ws, task)
        run_runner("verify", "--workspace", ws, "--task", task)
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["accepted"])


if __name__ == "__main__":
    unittest.main()
