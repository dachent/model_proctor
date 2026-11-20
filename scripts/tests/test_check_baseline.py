#!/usr/bin/env python3
"""check_baseline.compare: CI must not go green by losing tests (Plan 1 T3).

Run: python -m unittest discover -s scripts/tests -v
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_baseline.py"


def load():
    spec = importlib.util.spec_from_file_location("_check_baseline_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BASE = {"a": {"ran": 10, "skipped": 1, "failed": 0},
        "b": {"ran": 5, "skipped": 0, "failed": 0}}


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.compare = load().compare

    def test_fewer_tests_fails(self):
        cur = {"a": {"ran": 9, "skipped": 1, "failed": 0}, "b": BASE["b"]}
        self.assertEqual(self.compare(BASE, cur), ["a: ran 9 < baseline 10"])

    def test_new_skip_fails(self):
        cur = {"a": BASE["a"], "b": {"ran": 5, "skipped": 1, "failed": 0}}
        self.assertEqual(self.compare(BASE, cur), ["b: skipped 1 > baseline 0"])

    def test_zero_tests_fails(self):
        cur = {"a": {"ran": 0, "skipped": 0, "failed": 0}, "b": BASE["b"]}
        self.assertEqual(self.compare(BASE, cur), ["a: no tests"])

    def test_missing_suite_is_no_tests(self):
        self.assertEqual(self.compare(BASE, {"a": BASE["a"]}), ["b: no tests"])

    def test_equal_or_more_passes(self):
        cur = {"a": {"ran": 12, "skipped": 1, "failed": 0},
               "b": BASE["b"], "new-suite": {"ran": 3, "skipped": 0, "failed": 0}}
        self.assertEqual(self.compare(BASE, cur), [])


class CliTest(unittest.TestCase):
    def _run(self, current):
        with tempfile.TemporaryDirectory() as d:
            b, c = Path(d) / "b.json", Path(d) / "c.json"
            b.write_text(json.dumps(BASE))
            c.write_text(json.dumps(current))
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--baseline", str(b), "--current", str(c)],
                capture_output=True, text=True)

    def test_exit_1_on_violation(self):
        p = self._run({"a": {"ran": 1, "skipped": 0, "failed": 0}, "b": BASE["b"]})
        self.assertEqual(p.returncode, 1)
        self.assertIn("a: ran 1 < baseline 10", p.stdout)

    def test_exit_0_when_clean(self):
        self.assertEqual(self._run(BASE).returncode, 0)


if __name__ == "__main__":
    unittest.main()
