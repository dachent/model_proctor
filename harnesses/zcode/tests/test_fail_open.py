"""Fail-open must reach acceptance, or it is a field with no consumer.

The shims allow a tool call they could not adjudicate — that is deliberate, so a
flaky hook never bricks a session. The bounded half is that acceptance then
refuses. Without this test the contract would claim a guarantee the code does not
provide, which is precisely the failure #36 and #58 are about.
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

HARNESS = Path(__file__).resolve().parents[1]
ZPROCTOR = HARNESS / "zproctor.py"
GUARD = HARNESS / "hooks" / "zproctor_guard.mjs"
NODE = os.environ.get("ZPROCTOR_NODE", r"C:/Program Files/nodejs/node.exe")
HAS_NODE = Path(NODE).is_file()


class FailOpenIsBounded(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zproctor-fo-"))
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        self.state = self.tmp / "state"
        self.env = {**os.environ, "ZPROCTOR_STATE_ROOT": str(self.state)}
        (self.ws / "check.py").write_text("print('OK')\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def zp(self, *args):
        r = subprocess.run([sys.executable, str(ZPROCTOR), *args],
                           env=self.env, capture_output=True, text=True)
        return json.loads(r.stdout)

    def prepare_green(self):
        self.zp("lane", "--task", "t", "--workspace", str(self.ws), "--bounded")
        self.zp("init", "--task", "t", "--workspace", str(self.ws),
                "--verifier", "python", "check.py")
        self.assertTrue(self.zp("verify", "--task", "t",
                                "--workspace", str(self.ws))["passed"])

    def test_accept_succeeds_when_no_gap_opened(self):
        self.prepare_green()
        self.assertTrue(self.zp("accept", "--task", "t",
                                "--workspace", str(self.ws))["ok"])

    def test_accept_refuses_after_a_fail_open(self):
        self.prepare_green()
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "gate-failed-open.log").write_text(
            f"{int(time.time() * 1000)}\tdeadline\n", encoding="utf-8")
        out = self.zp("accept", "--task", "t", "--workspace", str(self.ws))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "gate_failed_open")

    def test_a_gap_from_before_the_task_does_not_block_it(self):
        """Only gaps opened during this task count; older markers are someone
        else's problem, already accepted or already abandoned."""
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "gate-failed-open.log").write_text(
            "1000\tdeadline\n", encoding="utf-8")   # epoch 1970, long before init
        self.prepare_green()
        self.assertTrue(self.zp("accept", "--task", "t",
                                "--workspace", str(self.ws))["ok"])

    @unittest.skipUnless(HAS_NODE, "node not present")
    def test_shim_records_the_gap_it_opened(self):
        """Hold stdin open so the guard hits its deadline and allows blind."""
        env = {**self.env, "ZPROCTOR_GUARD_DEADLINE_MS": "300"}
        p = subprocess.Popen([NODE, str(GUARD)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, env=env, text=True)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and p.poll() is None:
            time.sleep(0.05)
        if p.poll() is None:
            p.kill()
            self.fail("guard hung instead of failing open")
        self.assertEqual(p.stdout.read().strip(), "",
                         "a fail-open must allow, i.e. emit nothing")
        log = self.state / "gate-failed-open.log"
        self.assertTrue(log.is_file(), "the gap was allowed but never recorded")
        self.assertIn("deadline", log.read_text(encoding="utf-8"))


class VerifierHygiene(unittest.TestCase):
    def test_verifier_runs_without_writing_bytecode(self):
        """Identical-length sources written in the same second let CPython reuse a
        stale .pyc, and a failing verify then reported next: accept."""
        tmp = Path(tempfile.mkdtemp(prefix="zproctor-bc-"))
        try:
            ws = tmp / "ws"
            ws.mkdir()
            env = {**os.environ, "ZPROCTOR_STATE_ROOT": str(tmp / "state")}
            (ws / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            (ws / "check.py").write_text(
                "from app import VALUE\nassert VALUE == 2\nprint('OK')\n",
                encoding="utf-8")

            def zp(*args):
                r = subprocess.run([sys.executable, str(ZPROCTOR), *args],
                                   env=env, capture_output=True, text=True)
                return json.loads(r.stdout)

            zp("lane", "--task", "b", "--workspace", str(ws), "--bounded")
            zp("init", "--task", "b", "--workspace", str(ws),
               "--verifier", "python", "check.py")
            self.assertFalse(zp("verify", "--task", "b", "--workspace", str(ws))["passed"])
            # same byte length, same second - the stale-pyc trap
            (ws / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
            self.assertTrue(zp("verify", "--task", "b", "--workspace", str(ws))["passed"],
                            "stale bytecode was reused: the fix must be honoured")
            self.assertFalse((ws / "__pycache__").exists(),
                             "verifier wrote bytecode despite PYTHONDONTWRITEBYTECODE")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
