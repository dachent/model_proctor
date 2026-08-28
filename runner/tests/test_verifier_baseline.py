#!/usr/bin/env python3
"""A verifier that passes on the unmodified init tree is non-discriminating (#52).

Such a verifier will pass whatever the worker does, so acceptance carries no
information. The repo's own production-guard fixture is exactly that shape:
`["{python}", "-c", "print('ok')"]`.

`cmd_init` deliberately does NOT run the verifier to detect this — see the
comment in cmd_verify. Running it at init would write a receipt, and
`cmd_accept` never consults `len(state["dispatches"])`, so `init` -> `accept`
would succeed with zero dispatches on precisely the task class being detected;
a red baseline would also seed `state["failures"]` and pull a lateral switch
forward by one dispatch (flash -> glm is an ~8x unit-cost jump).

Instead the baseline is recognised when it occurs naturally: if the tree has
not moved since init, this verify already IS the baseline run. Zero extra
executions, and it gives `init_tree_sig` — written at init and previously read
nowhere — a consumer.

Reported, never enforced. Some tasks legitimately pass at init.

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


def run_runner(*argv, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=120)
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip() else {})


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-baseline-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_task(self, verifier=None):
        task = {
            "task_id": "t1",
            "prompt": "Fix the bug.",
            "features": {"bounded": True, "known_location": True,
                         "objective_acceptance": True},
            "scope": ["math_utils.py"],
            "verifier": verifier or {"argv": ["{python}", "check.py"]},
            "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60},
        }
        p = Path(self.tmp) / "task.json"
        p.write_text(json.dumps(task), encoding="utf-8")
        return str(p)

    def make_ws(self, buggy=True):
        ws = Path(self.tmp) / "ws"
        ws.mkdir()
        (ws / "math_utils.py").write_text(BUGGY if buggy else FIXED,
                                          encoding="utf-8")
        (ws / "check.py").write_text(CHECK, encoding="utf-8")
        return str(ws)

    def dispatch(self, ws, task, content=FIXED):
        return run_runner("dispatch", "--workspace", ws, "--task", task,
                          "--delegate", str(FAKE_WORKER),
                          env_extra={"FAKE_WORKER_WRITE": "math_utils.py",
                                     "FAKE_WORKER_CONTENT": content})


class BaselineDetectionTest(_Base):

    def test_trivial_verifier_on_untouched_tree_is_flagged(self):
        """The `print('ok')` shape: passes before anyone has done anything."""
        ws = self.make_ws()
        task = self.make_task(
            verifier={"argv": ["{python}", "-c", "print('ok')"]})
        run_runner("init", "--workspace", ws, "--task", task)

        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertTrue(ver["passed"])
        self.assertTrue(ver["baseline_tree"], "tree has not moved since init")
        self.assertTrue(ver["verifier_nondiscriminating"],
                        "a verifier passing on the init tree proves nothing")

    def test_real_verifier_on_untouched_tree_is_not_flagged(self):
        """Baseline tree, but the verifier correctly fails: it discriminates."""
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)

        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertFalse(ver["passed"], "the bug is still present")
        self.assertTrue(ver["baseline_tree"])
        self.assertFalse(ver["verifier_nondiscriminating"],
                         "failing on the unmodified tree is the whole point")

    def test_no_flag_once_the_worker_has_changed_the_tree(self):
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch(ws, task)

        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertTrue(ver["passed"])
        self.assertFalse(ver["baseline_tree"], "the worker moved the tree")
        self.assertFalse(ver["verifier_nondiscriminating"])

    def test_trivial_verifier_after_a_dispatch_is_not_flagged(self):
        """Honest limit: once the tree moves the signal is gone.

        The flag says "passed on an unmodified tree", not "this verifier is
        weak". A trivial verifier that first runs after a dispatch is not
        caught -- documenting that here so the flag is not over-read.
        """
        ws = self.make_ws()
        task = self.make_task(
            verifier={"argv": ["{python}", "-c", "print('ok')"]})
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch(ws, task)

        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertTrue(ver["passed"])
        self.assertFalse(ver["baseline_tree"])
        self.assertFalse(ver["verifier_nondiscriminating"])


class NoAcceptanceHoleTest(_Base):
    """The reason this is not implemented by running the verifier at init."""

    def test_init_alone_produces_no_receipt(self):
        ws = self.make_ws(buggy=False)          # verifier would pass at init
        task = self.make_task()
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        receipt = Path(out["state_dir"]) / "receipt-t1.json"
        self.assertFalse(receipt.is_file(),
                         "init must never write a receipt -- accept does not "
                         "check the dispatch count, so that would allow "
                         "init -> accept with zero dispatches")

    def test_init_then_accept_is_refused(self):
        ws = self.make_ws(buggy=False)
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1, f"accepted with zero dispatches: {out}")
        self.assertIn("no receipt", out.get("reason", ""))

    def test_baseline_detection_adds_no_failure_fingerprint(self):
        """A red baseline must not count toward stagnation.

        max_stagnant is 3; if the baseline seeded state["failures"] the
        lateral switch (flash -> glm, ~8x unit cost) would fire one dispatch
        early on every task whose verifier legitimately fails at init.
        """
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        rc, state = run_runner("status", "--workspace", ws)
        self.assertEqual(state["failures"], [],
                         "init must not manufacture a failure")


class LedgerTest(_Base):
    def test_flag_reaches_the_record_row(self):
        ws = self.make_ws()
        task = self.make_task(
            verifier={"argv": ["{python}", "-c", "print('ok')"]})
        run_runner("init", "--workspace", ws, "--task", task)
        run_runner("verify", "--workspace", ws, "--task", task)
        rc, out = run_runner("record", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["row"]["verifier_nondiscriminating"], out["row"])

    def test_clean_run_records_false(self):
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch(ws, task)
        run_runner("verify", "--workspace", ws, "--task", task)
        rc, out = run_runner("record", "--workspace", ws, "--task", task)
        self.assertIs(out["row"]["verifier_nondiscriminating"], False)


class InitTreeSigTest(_Base):
    def test_init_tree_sig_is_now_read(self):
        """It was written at init and read nowhere; this gives it a consumer."""
        src = (ROOT / "runner" / "runner.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            src.count("init_tree_sig"), 2,
            "init_tree_sig should be written AND read")


if __name__ == "__main__":
    unittest.main()
