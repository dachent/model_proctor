#!/usr/bin/env python3
"""Timeout-stack sizing authority contract tests (#108 TOOL-037).

core/timeout_stack.py is the ONE place margins are derived; harnesses import
it and never re-derive. These tests pin the derivation (the historical
"+ 120" is worst-case grace + overhead + report margin, not a chosen number)
and the ordering invariant #108's preflight must enforce.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import timeout_stack as ts  # noqa: E402


class Derivation(unittest.TestCase):
    def test_runner_margin_is_derived_not_chosen(self):
        # 120 = worst-case kill grace (60) + delegate overhead (30)
        #       + report margin (30).
        self.assertEqual(ts.RUNNER_BREAKER_MARGIN_S, 120.0)
        self.assertEqual(ts.RUNNER_BREAKER_MARGIN_S,
                         ts.KILL_GRACE_MAX_S + ts.DELEGATE_OVERHEAD_S
                         + ts.MIN_REPORT_MARGIN_S)

    def test_shipped_constants_form_a_coherent_stack(self):
        # Constants drift is now the ONLY way the shipped stack inverts;
        # this is the tripwire. Worst-case legal grace must still nest.
        self.assertEqual(
            ts.validate_stack(ts.stack_layers(1800, ts.KILL_GRACE_MAX_S)), [])

    def test_pilot_breaker_exceeds_runner_breaker_with_margin(self):
        self.assertGreaterEqual(
            ts.pilot_breaker_s(0),
            ts.runner_breaker_s(0) + ts.MIN_REPORT_MARGIN_S)

    def test_float_budgets_stay_float(self):
        # The schema allows timeout_s = 60.5; derivation and the layer
        # report must not truncate it.
        layers = ts.stack_layers(60.5, 5.0)
        self.assertTrue(all(isinstance(s, float) for _, _, s in layers))
        self.assertEqual(layers[1][2], 60.5 + 5.0 + ts.DELEGATE_OVERHEAD_S)


class OrderingInvariant(unittest.TestCase):
    def test_inverted_breaker_is_a_named_violation(self):
        layers = [("inner_self_abort", "self_abort", 100.0),
                  ("outer_breaker", "breaker", 100.0)]
        v = ts.validate_stack(layers)
        self.assertEqual(len(v), 1)
        self.assertIn("outer_breaker", v[0])
        self.assertIn("inner_self_abort", v[0])

    def test_breaker_inside_margin_is_refused(self):
        layers = [("inner_self_abort", "self_abort", 100.0),
                  ("outer_breaker", "breaker",
                   100.0 + ts.MIN_REPORT_MARGIN_S - 1)]
        self.assertEqual(len(ts.validate_stack(layers)), 1)

    def test_breaker_at_exact_margin_passes(self):
        layers = [("inner_self_abort", "self_abort", 100.0),
                  ("outer_breaker", "breaker", 100.0 + ts.MIN_REPORT_MARGIN_S)]
        self.assertEqual(ts.validate_stack(layers), [])

    def test_grace_beyond_delegate_cap_would_invert(self):
        # Why the delegate's grace cap (60) exists: grace 61 puts the
        # delegate ceiling inside the runner breaker's report margin.
        v = ts.validate_stack(ts.stack_layers(1800, ts.KILL_GRACE_MAX_S + 1))
        self.assertTrue(any("runner_breaker" in x for x in v))


if __name__ == "__main__":
    unittest.main()
