#!/usr/bin/env python3
"""TOOL-033 (#104): a detached payload's lifetime is outside every budget.

dispatch reports payload_running_detached without recording a failure and
closes its journal pair (a detached payload is not an orphan); accept refuses
while a detached payload may be mutating the tree, with a counted override;
status surfaces detached dispatches; pre-TOOL-033 state shapes read clean.

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
FAKE_WORKER = Path(__file__).resolve().parent / "fake_worker.py"

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
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def make_task(tmp, task_id="t1"):
    task = {
        "task_id": task_id,
        "prompt": "Fix the bug.",
        "features": {"bounded": True, "known_location": True,
                     "objective_acceptance": True},
        "scope": ["math_utils.py"],
        "verifier": {"argv": ["{python}", "check.py"]},
        "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60},
    }
    p = Path(tmp) / "task.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    return str(p)


def make_workspace(tmp):
    ws = Path(tmp) / "ws"
    ws.mkdir()
    (ws / "math_utils.py").write_text(BUGGY, encoding="utf-8")
    (ws / "check.py").write_text(CHECK, encoding="utf-8")
    return str(ws)


def journal_lines(sdir):
    jr = Path(sdir) / "journal.jsonl"
    if not jr.is_file():
        return []
    return [json.loads(ln) for ln in jr.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


class DetachedBudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-detached-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _setup_ready(self):
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp)
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        return ws, task, Path(out["state_dir"])

    def _dispatch_detached(self, ws, task):
        env = {"FAKE_WORKER_MODE": "detached",
               "FAKE_WORKER_WRITE": "math_utils.py",
               "FAKE_WORKER_CONTENT": FIXED}
        return run_runner("dispatch", "--workspace", ws, "--task", task,
                          "--delegate", str(FAKE_WORKER), env_extra=env)

    def test_detached_dispatch_is_reported_not_failed(self):
        ws, task, sdir = self._setup_ready()
        rc, out = self._dispatch_detached(ws, task)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["envelope_status"], "payload_running_detached")
        self.assertTrue(out["detached"])
        self.assertIsInstance(out["child_pid"], int)
        self.assertEqual(out["recommendation"]["action"], "monitor_detached")
        # Detached is a custody state, not a failure: nothing appended to
        # state["failures"], so the provider circuit breaker cannot trip on it.
        rc, st = run_runner("status", "--workspace", ws)
        self.assertEqual(rc, 0, st)
        self.assertEqual(st["failure_count"], 0, st)
        self.assertEqual(st["detached_dispatch_ids"], [out["dispatch_id"]])
        # The journal pair still closes — a detached payload is not an orphan.
        events = [e["event"] for e in journal_lines(sdir)
                  if e["event"].startswith("dispatch")]
        self.assertEqual(events, ["dispatch_open", "dispatch_finished"])
        self.assertTrue(journal_lines(sdir)[-1]["detached"])
        self.assertEqual(journal_lines(sdir)[-1]["envelope_status"],
                         "payload_running_detached")

    def test_accept_refuses_while_detached_payload_in_flight(self):
        ws, task, sdir = self._setup_ready()
        rc, out = self._dispatch_detached(ws, task)
        self.assertEqual(rc, 0, out)
        rc, out = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["passed"], out)
        # Journal pair is closed and the receipt is green, yet the payload
        # may still be writing: acceptance must refuse.
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1)
        self.assertFalse(out["accepted"])
        self.assertTrue(out["reason"].startswith("detached_payload_in_flight"),
                        out)
        # The reviewed-decision override works and is counted on state.
        rc, out = run_runner("accept", "--workspace", ws, "--task", task,
                             "--allow-detached-payload")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["accepted"])
        rc, st = run_runner("status", "--workspace", ws)
        self.assertEqual(st["allow_detached_payload_count"], 1)

    def test_pre_fix_state_without_detached_keys_reads_clean(self):
        # Review-focus #3: states written before TOOL-033 have no detached
        # keys anywhere; every new read must default to the legacy shape.
        ws, task, sdir = self._setup_ready()
        state_path = sdir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["dispatches"] = [{"agent": "glm-flash-worker",
                                "status": "completed",
                                "duration_seconds": 1.0,
                                "at": "2026-09-25T00:00:00"}]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        rc, st = run_runner("status", "--workspace", ws)
        self.assertEqual(rc, 0, st)
        self.assertEqual(st["detached_dispatch_ids"], [])
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertNotEqual(out.get("reason", "").startswith(
            "detached_payload_in_flight"), True)

    def test_unparseable_delegate_envelope_is_internal_error_not_crash(self):
        # Regression pin for the path TOOL-036 task 5 reworked (the pre-036
        # code referenced an undefined `r` here): a foreign envelope must
        # degrade to internal_error, not a NameError crash.
        ws, task, sdir = self._setup_ready()
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_WORKER),
                             env_extra={"FAKE_WORKER_MODE": "garbage"})
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["envelope_status"], "internal_error")
        self.assertEqual(out["failure_class"], "provider_or_tool")


if __name__ == "__main__":
    unittest.main()
