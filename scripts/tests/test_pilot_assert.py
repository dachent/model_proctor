#!/usr/bin/env python3
"""pilot.py --assert: the kimi-integration contracts (#54).

pilot.py already drives the whole loop against a real worker and appends an
evidence row, but nothing asserted on the row -- so a run that produced an
unparseable envelope, a null child_session_id, zero wire files or a null
api_cost_usd looked exactly like a clean one unless a human read the output.
The repo had a real-worker harness and no real-worker gate.

The paid run itself cannot be exercised here (Windows, credentials, real
money). The assertion LOGIC can, and that is the part that has to be right: a
gate that passes on a broken row is worse than no gate. Every contract below
is checked against a synthetic row shaped like the committed evidence in
evals/pilot-2026-08-25.jsonl.

Run: python -m unittest discover -s scripts/tests -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harnesses" / "kimi-code" / "runner"))
import pilot  # noqa: E402


def good_row(**over):
    """A row shaped like a healthy committed pilot result."""
    row = {
        "task_id": "sf1_off_by_one",
        "attempts": [{"agent": "ds-flash-worker", "lane": "flash",
                      "envelope_status": "completed", "verify_passed": True}],
        "accepted": True,
        "hidden_pass": True,
        "wire_files": 1,
        "child_session_ids": ["session_2f1c9a44-1111-2222-3333-444455556666"],
        "wire_coverage": {"records_parsed": 143, "records_unrecognized": 0,
                          "malformed_lines": 0, "unrecognized_types": {}},
        "record": {
            "dispatches": 1, "accepted": True, "usage_records": 4,
            "api_cost_usd": 0.007834,
            "tokens_by_model": {"fireworks/deepseek-v4-flash-0731": {
                "inputOther": 170445, "output": 77286,
                "inputCacheRead": 5255359, "inputCacheCreation": 0}},
        },
    }
    row.update(over)
    return row


class HealthyRowTest(unittest.TestCase):
    def test_clean_row_has_no_failures(self):
        self.assertEqual(pilot.check_contracts(good_row()), [],
                         "a healthy row must not trip the gate")


class ContractTest(unittest.TestCase):
    """Each contract must fire, and the message must name what broke."""

    def assertFires(self, row, needle):
        fails = pilot.check_contracts(row)
        self.assertTrue(fails, f"expected a failure mentioning {needle!r}")
        self.assertTrue(any(needle in f for f in fails),
                        f"no failure mentioned {needle!r}: {fails}")

    def test_no_attempts(self):
        self.assertFires(good_row(attempts=[]), "no dispatch attempt")

    def test_bad_envelope_status(self):
        self.assertFires(
            good_row(attempts=[{"envelope_status": "internal_error"}]),
            "envelope_status")

    def test_timeout_envelope_status(self):
        self.assertFires(good_row(attempts=[{"envelope_status": "timeout"}]),
                         "envelope_status")

    def test_missing_child_session_id(self):
        """The regex contract -- the single most fragile kimi coupling."""
        self.assertFires(good_row(child_session_ids=[]), "_CHILD_SESSION_RE")

    def test_no_wire_files(self):
        self.assertFires(good_row(wire_files=0), "wire.jsonl")

    def test_unrecognized_wire_records(self):
        self.assertFires(
            good_row(wire_coverage={"records_parsed": 100,
                                    "records_unrecognized": 33,
                                    "malformed_lines": 0,
                                    "unrecognized_types": {"token_counting.measured": 12}}),
            "unrecognized wire records")

    def test_malformed_wire_lines(self):
        self.assertFires(
            good_row(wire_coverage={"records_parsed": 100,
                                    "records_unrecognized": 0,
                                    "malformed_lines": 3,
                                    "unrecognized_types": {}}),
            "malformed")

    def test_coverage_unavailable(self):
        self.assertFires(good_row(wire_coverage=None), "coverage unavailable")

    def test_zero_usage_records(self):
        r = good_row()
        r["record"]["usage_records"] = 0
        self.assertFires(r, "usage_records == 0")

    def test_null_api_cost(self):
        r = good_row()
        r["record"]["api_cost_usd"] = None
        self.assertFires(r, "pricing.yaml")


class SilentRenameTest(unittest.TestCase):
    """The failure mode a null-check would miss entirely.

    A renamed usage field gives records > 0, tokens 0, and api_cost_usd == 0.0
    -- a number, not None. Any gate asking "did metering produce a value?"
    passes while every downstream cost is silently zero.
    """

    def test_renamed_field_is_caught_by_the_token_total(self):
        r = good_row()
        r["record"]["tokens_by_model"] = {
            "fireworks/deepseek-v4-flash-0731": {
                # kimi renamed inputOther; the old keys sum to nothing.
                "promptTokens": 170445, "inputOther": 0, "output": 0,
                "inputCacheRead": 0, "inputCacheCreation": 0}}
        r["record"]["api_cost_usd"] = 0.0
        fails = pilot.check_contracts(r)
        self.assertTrue(any("renamed" in f for f in fails), fails)

    def test_zero_cost_is_not_treated_as_present(self):
        r = good_row()
        r["record"]["api_cost_usd"] = 0.0
        self.assertTrue(any("priced at zero" in f
                            for f in pilot.check_contracts(r)))


class ProvenanceTest(unittest.TestCase):
    def test_provenance_keys_and_no_schema_version(self):
        p = pilot.provenance()
        for k in ("repo_commit", "installed_runner_sha256",
                  "installed_delegate_sha256", "kimi_exe_sha256"):
            self.assertIn(k, p)
        # SCHEMA_VERSION and delegate's schema_version are both the constant 1
        # and describe file formats, not builds — they would carry zero
        # attribution power on a QC row.
        self.assertNotIn("schema_version", p)

    def test_missing_install_yields_none_not_a_crash(self):
        """Provenance must degrade on a machine with no install."""
        p = pilot.provenance()
        for k in ("installed_runner_sha256", "kimi_exe_sha256"):
            self.assertTrue(p[k] is None or isinstance(p[k], str))


class CoverageHelperTest(unittest.TestCase):
    def test_no_wires_returns_none(self):
        self.assertIsNone(pilot.wire_coverage([]))

    def test_coverage_over_the_committed_fixture(self):
        fixture = ROOT / "evals" / "fixtures" / "wire" / "sample-wire.jsonl"
        if not fixture.is_file():
            self.skipTest("wire fixture not present (#53)")
        cov = pilot.wire_coverage([str(fixture)])
        self.assertIsNotNone(cov)
        self.assertEqual(cov["records_unrecognized"], 0)
        self.assertGreater(cov["records_parsed"], 0)


if __name__ == "__main__":
    unittest.main()
