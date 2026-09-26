#!/usr/bin/env python3
"""Kill-site audit (TOOL-036, issue #107): the registry is the law.

Every kill-primitive occurrence in scoped production files must match
killauthority.KILL_SITES exactly; source drift in either direction fails the
suite. Authority-class files (expected=None) are exempt from counts and are
guarded by literal markers instead.

Run: python -m unittest discover -s delegate/tests -v
"""

import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

_DELEGATE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DELEGATE_DIR))

import killauthority  # noqa: E402

REPO_ROOT = _DELEGATE_DIR.parents[2]

FORBIDDEN = {
    "proc_kill": r"\.kill\(\s*\)",
    "proc_terminate": r"\.terminate\(\s*\)",
    "taskkill": r"\btaskkill\b",
    "terminate_process": r"\bTerminateProcess\b",
    "os_kill": r"\bos\.kill\(",
}

VALID_CLASSIFICATIONS = {
    "authority", "custody_violation_documented", "external_readonly",
    "own_child_cleanup", "stdlib_implicit", "frozen_artifact", "policy_pure",
}


class TestKillSiteRegistry(unittest.TestCase):
    def test_registry_schema(self):
        for entry in killauthority.KILL_SITES:
            with self.subTest(site=entry["site"]):
                self.assertIn(entry["classification"], VALID_CLASSIFICATIONS)
                self.assertTrue(entry["file"])
                self.assertTrue(entry["note"])

    def test_registry_counts_match_source(self):
        for entry in killauthority.KILL_SITES:
            with self.subTest(site=entry["site"]):
                path = REPO_ROOT / entry["file"]
                self.assertTrue(path.is_file(), f"missing file: {entry['file']}")
                text = path.read_text(encoding="utf-8")
                for marker in entry.get("must_contain", []):
                    self.assertIn(marker, text,
                                  f"{entry['file']} lost marker {marker!r}")
                for marker in entry.get("must_not_contain", []):
                    self.assertNotIn(marker, text,
                                     f"{entry['file']} gained marker {marker!r}")
                expected = entry.get("expected")
                if expected is None:
                    continue  # authority-class: marker-guarded, not counted
                for name, pattern in FORBIDDEN.items():
                    actual = len(re.findall(pattern, text))
                    self.assertEqual(
                        actual, expected.get(name, 0),
                        f"{entry['file']}:{name} expected "
                        f"{expected.get(name, 0)}, found {actual} — re-survey "
                        f"and justify in KILL_SITES")


class _Recorder:
    """DI fakes recording every authority action in order."""

    def __init__(self):
        self.calls = []

    def runner(self, argv, **kwargs):
        self.calls.append(("tool", tuple(argv)))
        return subprocess.CompletedProcess(argv, 0)

    def job_close(self, job):
        self.calls.append(("job_close", job))

    def proc_close(self, handle):
        self.calls.append(("proc_close", handle))


def _make_authority(rec, grace=0, job="JOB", proc_handle="PH"):
    return killauthority.KillAuthority(
        1234, grace, job=job, proc_handle=proc_handle,
        job_close=rec.job_close, proc_close=rec.proc_close,
        tool_argv0="taskkill", tool_env=None, runner=rec.runner)


class TestKillAuthority(unittest.TestCase):
    def test_terminate_order_and_attribution(self):
        rec = _Recorder()
        authority = _make_authority(rec)
        self.assertEqual(authority.attribution, "none")
        attribution = authority.terminate("timeout")
        self.assertEqual(attribution, "delegate:timeout")
        self.assertEqual(authority.attribution, "delegate:timeout")
        self.assertEqual(rec.calls, [
            ("tool", ("taskkill", "/PID", "1234", "/T")),
            ("tool", ("taskkill", "/PID", "1234", "/T", "/F")),
            ("job_close", "JOB"),
            ("tool", ("taskkill", "/PID", "1234", "/T", "/F")),
            ("proc_close", "PH"),
        ])

    def test_terminate_without_job_still_kills_and_attributes(self):
        """job_warning=True path: no job handle, taskkill steps must still run."""
        rec = _Recorder()
        authority = _make_authority(rec, job=None)
        self.assertEqual(authority.terminate("interrupted"),
                         "delegate:interrupted")
        self.assertEqual(rec.calls, [
            ("tool", ("taskkill", "/PID", "1234", "/T")),
            ("tool", ("taskkill", "/PID", "1234", "/T", "/F")),
            ("tool", ("taskkill", "/PID", "1234", "/T", "/F")),
            ("proc_close", "PH"),
        ])

    def test_terminate_rejects_unknown_reason(self):
        rec = _Recorder()
        authority = _make_authority(rec)
        with self.assertRaises(ValueError):
            authority.terminate("annoyed")
        self.assertEqual(rec.calls, [])

    def test_terminate_is_idempotent(self):
        rec = _Recorder()
        authority = _make_authority(rec)
        authority.terminate("timeout")
        calls_after_first = list(rec.calls)
        self.assertEqual(authority.terminate("interrupted"),
                         "delegate:timeout")
        self.assertEqual(rec.calls, calls_after_first)

    def test_release_is_custody_close_only(self):
        rec = _Recorder()
        authority = _make_authority(rec)
        self.assertEqual(authority.release(), "none")
        self.assertEqual(authority.attribution, "none")
        self.assertEqual(rec.calls, [("proc_close", "PH"), ("job_close", "JOB")])
        authority.release()  # idempotent
        self.assertEqual(rec.calls, [("proc_close", "PH"), ("job_close", "JOB")])

    def test_terminate_after_release_is_a_noop(self):
        rec = _Recorder()
        authority = _make_authority(rec)
        authority.release()
        self.assertEqual(authority.terminate("timeout"), "none")
        self.assertEqual(len(rec.calls), 2)

    def test_tool_failure_is_swallowed(self):
        rec = _Recorder()

        def exploding_runner(argv, **kwargs):
            raise OSError("taskkill missing")

        authority = killauthority.KillAuthority(
            1234, 0, job="JOB", proc_handle="PH",
            job_close=rec.job_close, proc_close=rec.proc_close,
            tool_argv0="taskkill", tool_env=None, runner=exploding_runner)
        self.assertEqual(authority.terminate("timeout"), "delegate:timeout")
        self.assertIn(("job_close", "JOB"), rec.calls)


from test_delegate import DelegateTestBase, make_agent, _PID_SLEEPER  # noqa: E402


class TestKillAttributionEnvelope(DelegateTestBase):
    def test_completed_run_attributes_none(self):
        out, err, rc = self._run("test-agent", task="hello")
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertEqual(result["kill_authority"], "none")

    def test_failed_run_attributes_none(self):
        failer = self._script("failer", "import sys; sys.exit(3)\n")
        cfg = self._config({"test-agent": make_agent(failer)})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = self._assert_result(out, err, rc, "failed", 0)
        self.assertEqual(result["kill_authority"], "none")

    def test_timeout_run_attributes_delegate_timeout(self):
        sleeper = self._script("ka_sleeper", _PID_SLEEPER)
        pid_file = os.path.join(self.workspace, "ka_pid.txt")
        cfg = self._config({
            "test-agent": make_agent(
                sleeper, prompt_delivery="argument", extra_args=[pid_file],
                default_timeout=3, minimum_timeout=1, maximum_timeout=300),
        }, extra={"default_kill_grace_seconds": 1})
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        result = self._assert_result(out, err, rc, "timeout", 124)
        self.assertEqual(result["kill_authority"], "delegate:timeout")
        with open(pid_file, "r") as f:
            child_pid = int(f.read().strip())
        time.sleep(2)
        import delegate
        self.assertFalse(delegate.is_pid_alive(child_pid),
                         f"child {child_pid} alive after authority terminate")


if __name__ == "__main__":
    unittest.main()
