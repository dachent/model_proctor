#!/usr/bin/env python3
"""agents.allow_breakaway — validator, job-flag composition, and behaviour.

The flag shipped in b93bb4b with no test of any kind: not the validator
branch, not the flag composition, not the end-to-end path. It changes process
containment, which is the delegate's core safety property, so it gets one.

Windows-only mechanics are skipped elsewhere; the validator test is portable.

Run: python -m unittest discover -s delegate/tests -v
"""

import sys
import unittest
from pathlib import Path

_DELEGATE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DELEGATE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import delegate  # noqa: E402
from test_delegate import DelegateTestBase, make_agent  # noqa: E402

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

    def test_true_accepted(self):
        cfg = self._agent_with(True)
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


if __name__ == "__main__":
    unittest.main()
