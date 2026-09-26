#!/usr/bin/env python3
"""agents.allow_breakaway — validator, job-flag composition, and behaviour.

The flag shipped in b93bb4b with no test of any kind: not the validator
branch, not the flag composition, not the end-to-end path. It changes process
containment, which is the delegate's core safety property, so it gets one.

Windows-only mechanics are skipped elsewhere; the validator test is portable.

Run: python -m unittest discover -s delegate/tests -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

_DELEGATE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DELEGATE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import delegate  # noqa: E402
from test_delegate import (  # noqa: E402
    DelegateTestBase, make_agent, _ECHO_ARG, _EXIT_CODE, _PID_SLEEPER,
)

_IS_WINDOWS = sys.platform == "win32"


class TestBreakawayValidation(DelegateTestBase):
    """The validator must enforce bool, like every other typed agent key."""

    def _agent_with(self, value):
        a = make_agent(self.echo_script)
        a["allow_breakaway"] = value
        return self._config({"test-agent": a})

    def test_non_bool_rejected(self):
        for bad in ("true", 1, [], {}, None):
            with self.subTest(value=bad):
                cfg = self._agent_with(bad)
                out, err, rc = self._run("test-agent", task="hello", config=cfg)
                self._assert_result(out, err, rc, "invalid", 64)

    @unittest.skipUnless(_IS_WINDOWS, "breakaway dispatch requires WMI (Windows-only)")
    def test_true_accepted(self):
        a = make_agent(self.echo_script)
        a["prompt_delivery"] = "argument"
        a["allow_breakaway"] = True
        cfg = self._config({"test-agent": a})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "completed", 0)

    def test_false_accepted(self):
        cfg = self._agent_with(False)
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "completed", 0)

    def test_absent_defaults_to_false(self):
        """Omitting the key must keep the legacy containment guarantee."""
        cfg = self._config({"test-agent": make_agent(self.echo_script)})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "completed", 0)

    def test_stdin_delivery_rejected_with_breakaway(self):
        """WMI launch cannot pipe stdin; a breakaway+stdin config must be
        refused at load, not discovered as a hung child at dispatch."""
        a = make_agent(self.echo_script)  # prompt_delivery="stdin"
        a["allow_breakaway"] = True
        cfg = self._config({"test-agent": a})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = self._assert_result(out, err, rc, "invalid", 64)
        self.assertIn("prompt_delivery", result["error"])


@unittest.skipUnless(_IS_WINDOWS, "Job Objects are Windows-only")
class TestBreakawayJobFlags(unittest.TestCase):
    """create_kill_on_close_job composes LimitFlags correctly.

    Queried back from the kernel rather than asserted on the input struct, so
    the test fails if SetInformationJobObject silently rejects the flags.
    """

    def _limit_flags(self, allow_breakaway):
        import ctypes
        job = delegate.create_kill_on_close_job(allow_breakaway)
        try:
            info = delegate._JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            returned = ctypes.c_uint32(0)
            ok = delegate._k32.QueryInformationJobObject(
                job, delegate._JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned))
            self.assertTrue(ok, "QueryInformationJobObject failed")
            return info.BasicLimitInformation.LimitFlags
        finally:
            delegate.close_job(job)

    def test_default_is_kill_on_close_only(self):
        flags = self._limit_flags(False)
        self.assertTrue(flags & delegate._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        self.assertFalse(
            flags & delegate._JOB_OBJECT_LIMIT_BREAKAWAY_OK,
            "default path must not permit breakaway — the legacy guarantee is "
            "that everything dies with the worker")

    def test_opt_in_adds_breakaway_without_dropping_kill_on_close(self):
        flags = self._limit_flags(True)
        self.assertTrue(
            flags & delegate._JOB_OBJECT_LIMIT_BREAKAWAY_OK,
            "allow_breakaway=True must set JOB_OBJECT_LIMIT_BREAKAWAY_OK")
        self.assertTrue(
            flags & delegate._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            "breakaway must not clear kill-on-close for non-escaping children")

    def test_flag_constant_value(self):
        """0x0400 is JOB_OBJECT_LIMIT_BREAKAWAY_OK per the Win32 headers."""
        self.assertEqual(delegate._JOB_OBJECT_LIMIT_BREAKAWAY_OK, 0x0400)
        self.assertEqual(delegate._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, 0x2000)


@unittest.skipUnless(_IS_WINDOWS, "WMI launch is Windows-only")
class TestWmiSpawn(unittest.TestCase):
    """wmi_spawn_detached: real Win32_Process.Create round-trips, read back
    from the kernel (pid + exit code), not from the PowerShell stdout alone."""

    def test_spawn_returns_live_pid_and_exit_code(self):
        pid = delegate.wmi_spawn_detached("cmd.exe /c exit 0", tempfile.gettempdir())
        self.assertIsInstance(pid, int)
        self.assertGreater(pid, 0)
        handle = delegate.open_waitable_process(pid)
        try:
            rc = None
            deadline = time.time() + 30
            while rc is None and time.time() < deadline:
                rc = delegate.reap_handle(handle, 500)
            self.assertEqual(rc, 0, "cmd /c exit 0 must reap with exit code 0")
        finally:
            delegate.close_process_handle(handle)

    def test_bad_command_raises_custody_error(self):
        # Win32_Process.Create returns nonzero ReturnValue for a missing exe;
        # the helper must raise, never return a bogus pid or fall back.
        with self.assertRaises(delegate.CustodyError):
            delegate.wmi_spawn_detached(
                r"C:\no\such\exe-mp103-zzz.exe /c exit 0", tempfile.gettempdir())

    def test_bad_powershell_raises_custody_error(self):
        with unittest.mock.patch.object(
                delegate, "_POWERSHELL_EXE", r"C:\no\such\powershell-zzz.exe"):
            with self.assertRaises(delegate.CustodyError):
                delegate.wmi_spawn_detached("cmd.exe /c exit 0", tempfile.gettempdir())


class TestDetachedFailLoud(DelegateTestBase):
    """Fail-loud contract: when detached custody cannot be established the
    dispatch is internal_error and NOTHING is launched — never a silent
    fallback into the kill-on-close job (#103)."""

    def test_wmi_failure_is_internal_error_and_launches_nothing(self):
        agent = make_agent(self.echo_script, prompt_delivery="argument")
        agent["allow_breakaway"] = True
        run_dir, acl_warning = delegate.create_run_dir()
        try:
            with unittest.mock.patch.object(
                    delegate, "wmi_spawn_detached",
                    side_effect=delegate.CustodyError("simulated wmi outage")):
                result, exit_code = delegate._run_detached_dispatch(
                    "test-agent", agent,
                    {"default_kill_grace_seconds": 2,
                     "max_stdout_bytes": 65536,
                     "max_stderr_bytes": 65536},
                    [sys.executable, self.echo_script, "hello"],
                    self.workspace, {"PATH": os.environ.get("PATH", "")},
                    None, run_dir, acl_warning, 30, time.monotonic())
            self.assertEqual(result["status"], "internal_error")
            self.assertEqual(exit_code, delegate.EXIT_INTERNAL)
            self.assertIn("simulated wmi outage", result["error"])
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


@unittest.skipUnless(_IS_WINDOWS, "detached dispatch requires WMI (Windows-only)")
class TestDetachedDispatch(DelegateTestBase):
    """End-to-end detached dispatch through real delegate.py subprocesses."""

    def _detached_config(self, script, **kw):
        a = make_agent(script, prompt_delivery="argument", **kw)
        a["allow_breakaway"] = True
        return self._config({"test-agent": a})

    def test_completed_echo_roundtrip(self):
        script = self._script("echo_arg", _ECHO_ARG)
        cfg = self._detached_config(script)
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertEqual(result["stdout"], "hello")
        self.assertEqual(result["child_exit_code"], 0)

    def test_exit_code_propagates_through_bootstrap(self):
        script = self._script("exit3", _EXIT_CODE)
        cfg = self._detached_config(script, extra_args=["3"])
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = self._assert_result(out, err, rc, "failed", 0)
        self.assertEqual(result["child_exit_code"], 3)

    def test_timeout_kills_detached_payload(self):
        """Detachment changes WHO owns the payload when the LAUNCHER dies;
        the delegate's own timeout kill must still work while it lives."""
        pid_file = os.path.join(self.tmpdir, "timeout-pid.txt")
        script = self._script("sleeper-to", _PID_SLEEPER)
        a = make_agent(script, prompt_delivery="argument")
        a["allow_breakaway"] = True
        a["default_timeout"] = 5
        a["minimum_timeout"] = 5
        cfg = self._config({"test-agent": a})
        out, err, rc = self._run("test-agent", task=pid_file, timeout=5,
                                 config=cfg, timeout_wrap=180)
        self._assert_result(out, err, rc, "timeout", 124)
        with open(pid_file) as f:
            payload_pid = int(f.read().strip())
        deadline = time.time() + 30
        while delegate.is_pid_alive(payload_pid) and time.time() < deadline:
            time.sleep(0.5)
        self.assertFalse(delegate.is_pid_alive(payload_pid),
                         "timeout kill must still reach a detached payload's tree")


@unittest.skipUnless(_IS_WINDOWS, "detached custody is Windows-only")
class TestDetachedCustodyRegression(DelegateTestBase):
    """#103 regression signature: the runner kills the delegate
    (runner.py:738,754 proc.kill), the delegate's job handle closes, and
    KILL_ON_JOB_CLOSE wipes the worker tree. A detached payload must not be
    in that job at all. The paired control proves the cascade still fires
    for contained workers, so the survival assertion is load-bearing."""

    def _popen_delegate(self, agent, config, task):
        argv = [sys.executable, str(_DELEGATE_DIR / "delegate.py"),
                "--agent", agent, "--workspace", self.workspace,
                "--task", task, "--timeout", "60"]
        env = dict(os.environ)
        env["DELEGATE_CONFIG"] = config
        return subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env)

    def _launch_then_kill_launcher(self, allow_breakaway):
        tag = "detached" if allow_breakaway else "contained"
        pid_file = os.path.join(self.tmpdir, f"pid-{tag}.txt")
        script = self._script(f"sleeper-{tag}", _PID_SLEEPER)
        a = make_agent(script, prompt_delivery="argument")
        a["allow_breakaway"] = allow_breakaway
        a["default_timeout"] = 300
        a["minimum_timeout"] = 5
        a["maximum_timeout"] = 300
        cfg = self._config({"test-agent": a})
        proc = self._popen_delegate("test-agent", cfg, pid_file)
        try:
            deadline = time.time() + 90  # WMI + PowerShell cold start can be slow
            while not os.path.exists(pid_file):
                if proc.poll() is not None:
                    out, err = proc.communicate()
                    self.fail(f"delegate exited early rc={proc.returncode}: "
                              f"{out.decode('utf-8', 'replace')!r} "
                              f"{err.decode('utf-8', 'replace')!r}")
                self.assertLess(time.time(), deadline,
                                f"{tag} payload never wrote its pid file")
                time.sleep(0.5)
            with open(pid_file) as f:
                payload_pid = int(f.read().strip())
            proc.kill()  # exactly what runner.run_delegate does on wrapper timeout
            proc.wait(timeout=30)
            return payload_pid
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)

    def _kill_payload(self, pid):
        subprocess.run([delegate._TASKKILL_EXE, "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=15,
                       env=delegate._MINIMAL_TOOL_ENV)

    def test_detached_payload_survives_launcher_death(self):
        payload_pid = self._launch_then_kill_launcher(allow_breakaway=True)
        try:
            time.sleep(2)  # let any job-close cascade land before asserting
            self.assertTrue(
                delegate.is_pid_alive(payload_pid),
                "detached payload died with its launcher — #103 regression")
        finally:
            self._kill_payload(payload_pid)

    def test_contained_payload_dies_with_launcher(self):
        """Control: without allow_breakaway the job-close cascade must still
        kill the worker — otherwise the survival test above proves nothing."""
        payload_pid = self._launch_then_kill_launcher(allow_breakaway=False)
        try:
            deadline = time.time() + 15
            while delegate.is_pid_alive(payload_pid) and time.time() < deadline:
                time.sleep(0.5)
            self.assertFalse(
                delegate.is_pid_alive(payload_pid),
                "contained payload survived launcher death — the "
                "kill-on-close guarantee is broken")
        finally:
            self._kill_payload(payload_pid)


if __name__ == "__main__":
    unittest.main()
