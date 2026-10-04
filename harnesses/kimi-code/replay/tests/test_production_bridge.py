#!/usr/bin/env python3
"""Bridge tests: production predicate resolution (TOOL-039, #110)."""
import sys
import tempfile
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

from production_bridge import DEFAULT_SPEC, load_production_decide  # noqa: E402


class TestBridge(unittest.TestCase):
    def test_env_override_loads_module_function(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "fake_monitor.py").write_text(
                "def decide_kill(window):\n"
                "    return {'kill': False, 'reason': 'insufficient_evidence',\n"
                "            'measurement_degraded': False, 'evidence': {}}\n",
                encoding="utf-8")
            sys.path.insert(0, tmp)
            try:
                decide = load_production_decide(
                    {"REPLAY_PREDICATE": "fake_monitor:decide_kill"})
            finally:
                sys.path.remove(tmp)
        self.assertIsNotNone(decide)
        verdict = decide({"now_s": 0, "dispatch": {}, "events": []})
        self.assertFalse(verdict["kill"])

    def test_missing_module_returns_none(self):
        self.assertIsNone(load_production_decide(
            {"REPLAY_PREDICATE": "no_such_module_zzz_replay:decide_kill"}))

    def test_malformed_spec_raises(self):
        with self.assertRaises(ValueError):
            load_production_decide({"REPLAY_PREDICATE": "no-colon-here"})

    def test_default_spec_abstains_until_106_lands(self):
        # monitor.py ships with #106; before that the bridge must return None
        # (suite skips), after that a callable (suite runs).
        decide = load_production_decide({})
        self.assertTrue(decide is None or callable(decide))
        self.assertEqual(DEFAULT_SPEC, "monitor:decide_kill")


if __name__ == "__main__":
    unittest.main()
