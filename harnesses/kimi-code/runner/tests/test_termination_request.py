#!/usr/bin/env python3
"""TOOL-036: the runner is read-only — request, bounded wait, report, never kill.

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
RUNNER_DIR = ROOT / "runner"
RUNNER = RUNNER_DIR / "runner.py"
FAKE_DELEGATE = Path(__file__).resolve().parent / "fake_delegate_termreq.py"

sys.path.insert(0, str(RUNNER_DIR))
import runner  # noqa: E402

# Fast clocks for the test: budget timeout 1s, wrapper grace 2s, request wait 3s.
ENV_FAST = {"MP_WRAPPER_GRACE_S": "2", "MP_TERMINATION_REQUEST_WAIT_S": "3"}

BUGGY = 'def sum_to_n(n):\n    return sum(range(1, n))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15\n'
    'print("PASS")\n'
)


def run_runner(*argv, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=180)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def make_task(tmp, task_id="t1", timeout_s=1):
    task = {
        "task_id": task_id,
        "prompt": "Fix the bug.",
        "features": {"bounded": True, "known_location": True,
                     "objective_acceptance": True},
        "scope": ["math_utils.py"],
        "verifier": {"argv": ["{python}", "check.py"]},
        "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": timeout_s},
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


def _pid_alive(pid):
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    k32.CloseHandle(handle)
    return True


class TerminationRequestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-termreq-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _setup_ready(self):
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp)
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        return ws, task, Path(out["state_dir"])

    def test_request_write_failure_is_reported_not_fatal(self):
        """Review Focus #3: a wedged request channel yields a clean outcome."""
        bad_path = str(Path(self.tmp) / "no" / "such" / "dir" / "req")
        outcome, out, err = runner._request_termination(None, bad_path, 1)
        self.assertEqual(outcome, "request_write_failed")
        self.assertEqual((out, err), ("", ""))
        outcome, _, _ = runner._request_termination(None, None, 1)
        self.assertEqual(outcome, "no_request_channel")

    def test_runner_requests_and_delegate_executes(self):
        ws, task, sdir = self._setup_ready()
        sidecar = Path(self.tmp) / "sidecar.json"
        env = dict(ENV_FAST, FAKE_DELEGATE_MODE="honor",
                   FAKE_DELEGATE_SIDECAR=str(sidecar))
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_DELEGATE), env_extra=env)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["envelope_status"], "interrupted", out)
        lines = journal_lines(sdir)
        term = [e for e in lines
                if e["event"] == "dispatch_termination_requested"]
        self.assertEqual(len(term), 1, lines)
        self.assertEqual(term[0]["outcome"], "delegate_executed")
        self.assertEqual(term[0]["for_dispatch_id"], out["dispatch_id"])
        self.assertNotIn("dispatch_id", term[0],
                         "a bare dispatch_id masks dispatch_open in "
                         "_journal_open (runner.py:579-590)")
        fin = [e for e in lines if e["event"] == "dispatch_finished"]
        self.assertEqual(fin[0]["kill_authority"], "delegate:runner_requested")
        # Stale-file defense (Review Focus #2): the request file did not exist
        # when the delegate started.
        self.assertFalse(
            json.loads(sidecar.read_text())["request_file_existed_at_start"])
        # The open entry paired off: nothing reported in flight.
        status_rc, status_out = run_runner("status", "--workspace", ws)
        self.assertEqual(status_rc, 0, status_out)
        self.assertEqual(status_out["open_journal_ids"], [], status_out)

    @unittest.skipUnless(sys.platform == "win32", "pid liveness probe is Windows-only")
    def test_runner_never_kills_wedged_delegate(self):
        ws, task, sdir = self._setup_ready()
        pid_file = Path(self.tmp) / "fake_delegate.pid"
        env = dict(ENV_FAST, FAKE_DELEGATE_MODE="ignore",
                   FAKE_DELEGATE_PID_FILE=str(pid_file))
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_DELEGATE), env_extra=env)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["envelope_status"], "timeout", out)
        lines = journal_lines(sdir)
        term = [e for e in lines
                if e["event"] == "dispatch_termination_requested"]
        self.assertEqual(len(term), 1, lines)
        # The runner's emit contract reserves the "error" key for refusals
        # (existing suites assertNotIn it); the wedged outcome's error string
        # is reported on the journal record instead of the dispatch emit.
        self.assertEqual(term[0]["reason"], "delegate_wrapper_timeout")
        self.assertEqual(term[0]["outcome"], "unresolved")
        fin = [e for e in lines if e["event"] == "dispatch_finished"]
        self.assertEqual(fin[0]["kill_authority"], "unresolved_reported")
        # THE invariant: the runner reported instead of killing — the wedged
        # delegate is still alive after the runner returned.
        pid = int(pid_file.read_text().strip())
        try:
            self.assertTrue(_pid_alive(pid),
                            f"runner killed delegate pid {pid} — read-only "
                            f"contract broken")
        finally:
            # Test-side cleanup of its own fixture is fine.
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True)


if __name__ == "__main__":
    unittest.main()
