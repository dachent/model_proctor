#!/usr/bin/env python3
"""T2: a nondiscriminating green receipt is refused at ANY dispatch_seq.

The pre-fix gate consumed ``verifier_nondiscriminating`` only when
``dispatch_seq == 0`` (the S4 zero-dispatch hole). Escalation audit 2026-10-29
(S1-g, "the single cleanest trigger in the session") found the other half:
verify returned ``passed: true`` AND ``verifier_nondiscriminating: true`` at
``dispatch_seq`` 1, and accept certified the task anyway. The receipt still
cannot distinguish done from undone — the dispatch count is not evidence that
it can — so acceptance on it is a reviewed decision, never a silent one.

The shape is reproduced exactly: a workspace that already passes at init, a
dispatch whose worker changes nothing (so the tree at verify time is still the
init tree), then a green, nondiscriminating receipt at dispatch_seq 1.

Run: python -m unittest discover -s harnesses/kimi-code/runner/tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "runner.py"
FAKE_WORKER = Path(__file__).resolve().parent / "fake_worker.py"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hermetic_scratch  # noqa: E402  (#115: keep dispatch scratch in temp)

hermetic_scratch.install()

FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15, f"sum_to_n(5)={sum_to_n(5)}, expected 15"\n'
    'print("PASS")\n'
)


def run_runner(*argv, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=120)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


class AcceptNondiscriminatingTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="nd-accept-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _setup_passing_at_init(self):
        """Workspace already green at init, so a no-op dispatch verifies green
        on the unmodified init tree — the S1-g receipt shape at seq 1."""
        ws = self.tmp / "ws"
        ws.mkdir()
        (ws / "math_utils.py").write_text(FIXED, encoding="utf-8")
        (ws / "check.py").write_text(CHECK, encoding="utf-8")
        task = self.tmp / "task.json"
        task.write_text(json.dumps({
            "task_id": "t1",
            "prompt": "Fix the bug.",
            "features": {"bounded": True, "known_location": True,
                         "objective_acceptance": True},
            "scope": ["math_utils.py"],
            "verifier": {"argv": ["{python}", "check.py"]},
            "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60},
        }), encoding="utf-8")
        return str(ws), str(task)

    def _green_nondiscriminating_at_seq_one(self):
        ws, task = self._setup_passing_at_init()
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        # Default fake-worker mode: completes without touching the tree.
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_WORKER))
        self.assertEqual(rc, 0, out)
        rc, out = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["passed"], out)
        self.assertTrue(out["verifier_nondiscriminating"], out)
        self.assertEqual(out["dispatch_seq"], 1, out)
        return ws, task

    def test_accept_refuses_nondiscriminating_green_with_a_dispatch(self):
        ws, task = self._green_nondiscriminating_at_seq_one()
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertNotEqual(rc, 0, out)
        self.assertFalse(out["accepted"], out)
        self.assertTrue(out["reason"].startswith("nondiscriminating_receipt"),
                        out)

    def test_override_is_counted_on_state(self):
        ws, task = self._green_nondiscriminating_at_seq_one()
        rc, out = run_runner("accept", "--workspace", ws, "--task", task,
                             "--allow-nondiscriminating")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["accepted"], out)
        rc, out = run_runner("status", "--workspace", ws)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["allow_nondiscriminating_count"], 1, out)
        # The narrow zero-dispatch override keeps its own, separate count.
        self.assertEqual(out.get("allow_zero_dispatch_count", 0), 0, out)

    def test_zero_dispatch_override_still_governs_only_its_own_case(self):
        """--allow-zero-dispatch must not launder a dispatched receipt."""
        ws, task = self._green_nondiscriminating_at_seq_one()
        rc, out = run_runner("accept", "--workspace", ws, "--task", task,
                             "--allow-zero-dispatch")
        self.assertNotEqual(rc, 0, out)
        self.assertTrue(out["reason"].startswith("nondiscriminating_receipt"),
                        out)


if __name__ == "__main__":
    unittest.main()
