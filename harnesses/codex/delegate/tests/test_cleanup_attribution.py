"""TOOL-036: the codex adapter attributes its own-child cleanup kills."""
import subprocess
import sys
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

import delegate  # noqa: E402


class _FakeProc:
    def __init__(self, wait_results):
        self._wait_results = list(wait_results)
        self.kill_calls = 0
        self.stdin = None
        self.stdout = None

    def wait(self, timeout=None):
        result = self._wait_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def kill(self):
        self.kill_calls += 1


class TestCleanupAttribution(unittest.TestCase):
    def test_kill_is_attributed(self):
        proc = _FakeProc([subprocess.TimeoutExpired("fake", 1), 0])
        record = {}
        self.assertEqual(delegate._cleanup(proc, record=record), 0)
        self.assertEqual(proc.kill_calls, 1)
        self.assertEqual(record["kill_authority"], "codex:cleanup_kill")

    def test_clean_wait_is_not_attributed(self):
        proc = _FakeProc([0])
        record = {}
        self.assertEqual(delegate._cleanup(proc, record=record), 0)
        self.assertEqual(proc.kill_calls, 0)
        self.assertNotIn("kill_authority", record)

    def test_attribution_survives_reap_failure(self):
        """The record is stamped at kill time, before any CleanupFailure."""
        proc = _FakeProc([subprocess.TimeoutExpired("fake", 1), OSError("wedged")])
        record = {}
        with self.assertRaises(delegate.CleanupFailure):
            delegate._cleanup(proc, record=record)
        self.assertEqual(record["kill_authority"], "codex:cleanup_kill")


if __name__ == "__main__":
    unittest.main()
