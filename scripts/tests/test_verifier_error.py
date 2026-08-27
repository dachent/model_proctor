#!/usr/bin/env python3
"""verifier_error.py — contingency maths and the guards around it (#46).

The tool's value is entirely in not being misread, so most of these tests are
about the guards rather than the arithmetic: denominators reported explicitly,
joint and conditional kept apart, underpowered samples labelled, corpora not
pooled, and rows missing a field counted rather than coerced to False.

Run: python -m unittest discover -s scripts/tests -v
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "evals" / "verifier_error.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("_verifier_error_under_test",
                                                  TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ContingencyTest(unittest.TestCase):
    def setUp(self):
        self.m = load_tool()

    def rows(self, spec):
        """spec: list of (accepted, hidden_pass)."""
        return [{"accepted": a, "hidden_pass": h} for a, h in spec]

    def test_counts_land_in_the_right_cells(self):
        t = self.m.contingency(self.rows([
            (True, True), (True, False), (False, True), (False, False),
            (True, False),
        ]), "accepted")
        self.assertEqual(t["a_h"], 1)
        self.assertEqual(t["a_not_h"], 2)
        self.assertEqual(t["not_a_h"], 1)
        self.assertEqual(t["not_a_not_h"], 1)
        self.assertEqual(t["skipped"], 0)

    def test_joint_and_conditional_differ_and_use_stated_denominators(self):
        # 1 accepted-and-hidden-failed, 1 refused-and-hidden-failed, 8 clean.
        t = self.m.contingency(
            self.rows([(True, False), (False, False)] + [(True, True)] * 8),
            "accepted")
        r = self.m.rates(t)
        self.assertEqual(r["n"], 10)
        self.assertEqual(r["hidden_failures"], 2)
        self.assertAlmostEqual(r["joint"], 0.1)          # 1/10
        self.assertAlmostEqual(r["conditional"], 0.5)    # 1/2
        self.assertNotEqual(r["joint"], r["conditional"],
                            "the two rates must not be conflated")

    def test_conditional_undefined_when_hidden_never_failed(self):
        r = self.m.rates(self.m.contingency(
            self.rows([(True, True)] * 5), "accepted"))
        self.assertIsNone(r["conditional"],
                          "a rate over an empty denominator must be None, not 0")
        self.assertEqual(r["joint"], 0.0)

    def test_missing_fields_are_skipped_not_coerced(self):
        """A row without hidden_pass must not silently count as a failure."""
        rows = [{"accepted": True}, {"hidden_pass": True},
                {"accepted": True, "hidden_pass": False}]
        t = self.m.contingency(rows, "accepted")
        self.assertEqual(t["skipped"], 2)
        self.assertEqual(self.m.rates(t)["n"], 1)

    def test_perfect_verifier_reads_zero(self):
        """Every hidden failure also refused: conditional 0."""
        r = self.m.rates(self.m.contingency(
            self.rows([(False, False)] * 3 + [(True, True)] * 7), "accepted"))
        self.assertEqual(r["conditional"], 0.0)

    def test_blind_verifier_reads_one(self):
        """Accepts everything, including all hidden failures: conditional 1."""
        r = self.m.rates(self.m.contingency(
            self.rows([(True, False)] * 3 + [(True, True)] * 7), "accepted"))
        self.assertEqual(r["conditional"], 1.0)


class PowerGuardTest(unittest.TestCase):
    def setUp(self):
        self.m = load_tool()

    def test_threshold_is_stated_and_matches_the_prereg(self):
        self.assertEqual(self.m.MIN_EVENTS_FOR_0_1, 30)
        prereg = (ROOT / "evals" / "PREREG-verifier-error.md").read_text(
            encoding="utf-8")
        self.assertIn("30", prereg,
                      "the power rule must be fixed in the PREREG, not only in code")

    def test_real_corpora_are_all_underpowered(self):
        """Guards the headline claim of the README correction.

        If a future corpus lands with >= 30 hidden failures this test fails on
        purpose: the 'cannot resolve verifier error' conclusion would no longer
        hold and both the README and the PREREG decision rule need revisiting.
        """
        for name, field, _ in self.m.KNOWN:
            path = ROOT / "evals" / name
            if not path.is_file():
                continue
            rows = self.m.load(path)
            f = field if (rows and field in rows[0]) else "acceptance_pass"
            r = self.m.rates(self.m.contingency(rows, f))
            self.assertLess(
                r["hidden_failures"], self.m.MIN_EVENTS_FOR_0_1,
                f"{name} now has {r['hidden_failures']} hidden failures — "
                "revisit the README correction and the PREREG decision rule")


class ReportedNumbersTest(unittest.TestCase):
    """The specific figures quoted in the README correction."""

    def setUp(self):
        self.m = load_tool()

    def test_results_jsonl_matches_the_readme_table(self):
        rows = self.m.load(ROOT / "evals" / "results.jsonl")
        t = self.m.contingency(rows, "acceptance_pass")
        r = self.m.rates(t)
        self.assertEqual(r["n"], 36)
        self.assertEqual(t["a_h"] + t["a_not_h"], 36, "acceptance_pass 36/36")
        self.assertEqual(t["a_h"] + t["not_a_h"], 27, "hidden_pass 27/36")
        self.assertEqual(t["a_not_h"], 9, "9 accepted-but-hidden-failed")
        self.assertEqual(r["conditional"], 1.0,
                         "the acceptance check caught 0 of 9")
        self.assertEqual(r["joint"], 0.25)

    def test_showcase_split_matches_the_readme_table(self):
        rows = [r for r in self.m.load(ROOT / "evals" / "results.jsonl")
                if r.get("set") == "showcase"]
        self.assertEqual(len(rows), 18)
        self.assertEqual(sum(1 for r in rows if r["acceptance_pass"]), 18)
        self.assertEqual(sum(1 for r in rows if r["hidden_pass"]), 9)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.m = load_tool()

    def test_json_output_is_parseable_and_per_file(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.m.main(["--json"])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("results.jsonl", data)
        entry = data["results.jsonl"]
        for key in ("joint", "conditional", "n", "hidden_failures",
                    "acceptance_field"):
            self.assertIn(key, entry)

    def test_no_pooled_total_is_emitted(self):
        """Pooling across estimands is prohibited; the tool must not offer one."""
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.m.main(["--json"])
        data = json.loads(buf.getvalue())
        for forbidden in ("total", "pooled", "overall", "all"):
            self.assertNotIn(forbidden, data)


if __name__ == "__main__":
    unittest.main()
