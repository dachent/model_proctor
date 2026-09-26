#!/usr/bin/env python3
"""#109/TOOL-038: bounded workspace measurement + monitor isolation.

In-process on purpose: the wedge analog is a function that never returns
(a read syscall blocked on a dead OneDrive/SMB mount), and the only bound
for that class is the daemon-thread deadline — no pre-check can preempt a
blocked syscall. Also pins that monitor paths NEVER call tree measurement.

Run: python -m unittest discover -s harnesses/kimi-code/runner/tests -v
"""

import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runner"))

import runner  # noqa: E402

FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15, f"sum_to_n(5)={sum_to_n(5)}, expected 15"\n'
    'print("PASS")\n'
)


def _ns(**kw):
    defaults = dict(workspace=None, task=None, state_dir=None, reinit=False,
                    reset_provider_gate=False, agent_map=None, delegate=None,
                    ack=None, allow_zero_dispatch=False)
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def _run_cmd(fn, ns):
    """Call a runner cmd_* in-process; returns (exit_code, parsed_json)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = fn(ns)
        except SystemExit as e:
            rc = e.code
    return rc, json.loads(buf.getvalue())


class MeasureWithDeadlineTest(unittest.TestCase):
    def test_returns_result(self):
        self.assertEqual(
            runner._measure_with_deadline(lambda: 42, 5, "t"), 42)

    def test_propagates_exceptions(self):
        def boom():
            raise ValueError("nope")
        with self.assertRaises(ValueError):
            runner._measure_with_deadline(boom, 5, "t")

    def test_blocking_measurement_times_out_fast(self):
        t0 = time.monotonic()
        with self.assertRaises(runner.MeasurementTimeout) as cm:
            runner._measure_with_deadline(lambda: time.sleep(30), 0.2,
                                          "wedged-tree")
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(cm.exception.what, "wedged-tree")


class AcceptancePathsFailClosedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-mi-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        ws = Path(self.tmp) / "ws"
        ws.mkdir()
        (ws / "math_utils.py").write_text(FIXED, encoding="utf-8")
        (ws / "check.py").write_text(CHECK, encoding="utf-8")
        self.ws = str(ws)
        task = {"task_id": "t1", "prompt": "Fix the bug.",
                "features": {"bounded": True, "known_location": True,
                             "objective_acceptance": True},
                "scope": ["math_utils.py"],
                "verifier": {"argv": ["{python}", "check.py"]},
                "budget": {"max_dispatches": 4, "max_stagnant": 3,
                           "timeout_s": 60}}
        tp = Path(self.tmp) / "task.json"
        tp.write_text(json.dumps(task), encoding="utf-8")
        self.task = str(tp)
        rc, out = _run_cmd(runner.cmd_init,
                           _ns(workspace=self.ws, task=self.task))
        assert rc == 0, out

    def _wedged_tree_signature(self):
        orig_sig, orig_budget = runner.tree_signature, runner.MEASUREMENT_BUDGET_S

        class _Wedge:
            def __enter__(self):
                runner.tree_signature = lambda ws: time.sleep(30)
                runner.MEASUREMENT_BUDGET_S = 0.2

            def __exit__(self, *exc):
                runner.tree_signature = orig_sig
                runner.MEASUREMENT_BUDGET_S = orig_budget
        return _Wedge()

    def test_verify_refuses_closed_on_measurement_timeout(self):
        with self._wedged_tree_signature():
            rc, out = _run_cmd(runner.cmd_verify,
                               _ns(workspace=self.ws, task=self.task))
        self.assertEqual(rc, 1)
        self.assertEqual(out["passed"], False)
        self.assertEqual(out["rejected"], "measurement_timeout")

    def test_accept_refuses_on_measurement_timeout(self):
        rc, out = _run_cmd(runner.cmd_verify,
                           _ns(workspace=self.ws, task=self.task))
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["passed"], out)
        with self._wedged_tree_signature():
            rc, out = _run_cmd(runner.cmd_accept,
                               _ns(workspace=self.ws, task=self.task))
        self.assertEqual(rc, 1)
        self.assertEqual(out["accepted"], False)
        self.assertTrue(out["reason"].startswith("measurement_timeout"), out)

    def test_init_refuses_on_measurement_timeout(self):
        ws2 = Path(self.tmp) / "ws2"
        shutil.copytree(self.ws, ws2)
        with self._wedged_tree_signature():
            rc, out = _run_cmd(runner.cmd_init,
                               _ns(workspace=str(ws2), task=self.task))
        self.assertEqual(rc, 1)
        self.assertEqual(out["error"], "measurement_timeout")


class MonitorIsolationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-mon-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        ws = Path(self.tmp) / "ws"
        ws.mkdir()
        (ws / "math_utils.py").write_text(FIXED, encoding="utf-8")
        (ws / "check.py").write_text(CHECK, encoding="utf-8")
        self.ws = str(ws)
        task = {"task_id": "t1", "prompt": "Fix the bug.",
                "features": {"bounded": True, "known_location": True,
                             "objective_acceptance": True},
                "scope": ["math_utils.py"],
                "verifier": {"argv": ["{python}", "check.py"]},
                "budget": {"max_dispatches": 4, "max_stagnant": 3,
                           "timeout_s": 60}}
        tp = Path(self.tmp) / "task.json"
        tp.write_text(json.dumps(task), encoding="utf-8")
        self.task = str(tp)
        rc, out = _run_cmd(runner.cmd_init,
                           _ns(workspace=self.ws, task=self.task))
        assert rc == 0, out

    def test_status_never_measures_the_tree(self):
        def bomb(ws):
            raise AssertionError("monitor path touched the workspace tree")
        orig_sig, orig_surface = runner.tree_signature, runner.config_surface
        runner.tree_signature = bomb
        runner.config_surface = bomb
        try:
            rc, out = _run_cmd(runner.cmd_status, _ns(workspace=self.ws))
        finally:
            runner.tree_signature = orig_sig
            runner.config_surface = orig_surface
        self.assertEqual(rc, 0, out)
        self.assertIn("payload_progress", out)
        self.assertIn("measurement_degraded", out)
        self.assertIn("degraded_components", out)


import pilot  # noqa: E402  (same sys.path entry as runner)


class FindWiresDeadlineTest(unittest.TestCase):
    """#109: session-dir statting abstains with partial results past its
    deadline instead of wedging the pilot on a sync-deferred mount."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-wires-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        home = Path(self.tmp) / "home"
        self.wire = (home / "sessions" / "s" / "sid-1" / "agents" / "a"
                     / "wire.jsonl")
        self.wire.parent.mkdir(parents=True)
        self.wire.write_text("{}\n", encoding="utf-8")
        self.home = str(home)

    def test_expired_deadline_abstains_partial(self):
        self.assertEqual(
            pilot.find_wires(["sid-1"], 0, homes=[self.home],
                             deadline=time.monotonic() - 1), [])

    def test_no_deadline_finds_wire(self):
        found = pilot.find_wires(["sid-1"], 0, homes=[self.home])
        self.assertEqual([Path(p) for p in found], [self.wire])


if __name__ == "__main__":
    unittest.main()
