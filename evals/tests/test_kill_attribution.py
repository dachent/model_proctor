#!/usr/bin/env python3
"""TOOL-036: eval result rows attribute the harness's own timeout taskkill."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_eval  # noqa: E402


class TestEvalKillAttribution(unittest.TestCase):
    def test_timeout_row_attributes_eval_taskkill(self):
        row = run_eval._result_row("2026-09-25T00:00:00", 12.3, -1, True, 100, None)
        self.assertEqual(row["kill_authority"], "eval:taskkill_tree_force")
        self.assertEqual(row["agent_exit"], -1)
        self.assertTrue(row["timed_out"])

    def test_clean_row_attributes_none(self):
        row = run_eval._result_row("2026-09-25T00:00:00", 1.004, 0, False, 10, None)
        self.assertEqual(row["kill_authority"], "none")
        self.assertEqual(row["wall_clock_s"], 1.0)


if __name__ == "__main__":
    unittest.main()
