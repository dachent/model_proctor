#!/usr/bin/env python3
"""Schema tests for the replay corpus (TOOL-039, #110)."""
import sys
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

from replay_schema import CaseSchemaError, validate_case  # noqa: E402


def minimal_case():
    return {
        "schema_version": 1,
        "case_id": "2099-01-01-unit-minimal",
        "incident_date": "2099-01-01",
        "class": "completion_before_stall_kill",
        "source_note": "schema unit fixture",
        "dispatch": {"agent": "kimi-worker", "timeout_s": 1800},
        "events": [
            {"t": 0, "type": "dispatch_open"},
            {"t": 10, "type": "completion_evidence",
             "channel": "envelope_completed"},
        ],
        "expect": {"kill": False},
    }


def kill_expect():
    return {"kill": True, "reason": "confirmed_wedge",
            "kill_window_s": [2000, 2200], "evidence_keys": ["stack_frames"]}


class TestValidCases(unittest.TestCase):
    def test_minimal_case_validates_and_returns_input(self):
        case = minimal_case()
        self.assertIs(validate_case(case), case)

    def test_kill_expectation_shape(self):
        case = minimal_case()
        case["class"] = "control_genuine_wedge"
        case["expect"] = kill_expect()
        self.assertIs(validate_case(case), case)

    def test_failed_probe_carries_no_samples(self):
        case = minimal_case()
        case["events"].append({"t": 20, "type": "probe", "probe_ok": False})
        case["expect"] = {"kill": False, "measurement_degraded": True}
        self.assertIs(validate_case(case), case)

    def test_impossible_samples_are_legal_fixture_content(self):
        # 2026-09-16/17: negative CPU / impossible write ages must pass the
        # schema — detecting them is the predicate's job, not the loader's.
        case = minimal_case()
        case["class"] = "monitor_measurement_failure"
        case["events"].append({"t": 20, "type": "probe", "probe_ok": True,
                               "cpu_percent": -1.0, "last_write_age_s": 999999,
                               "stack_frames": None})
        self.assertIs(validate_case(case), case)


class TestRefusals(unittest.TestCase):
    def assert_refused(self, mutate, field):
        case = minimal_case()
        mutate(case)
        with self.assertRaises(CaseSchemaError) as ctx:
            validate_case(case)
        self.assertEqual(ctx.exception.field, field)

    def test_unknown_top_level_key(self):
        self.assert_refused(lambda c: c.update(verbatim_trace="x"), "case")

    def test_wrong_schema_version(self):
        self.assert_refused(lambda c: c.update(schema_version=2), "schema_version")

    def test_bool_schema_version(self):
        self.assert_refused(lambda c: c.update(schema_version=True), "schema_version")

    def test_unknown_class(self):
        self.assert_refused(lambda c: c.update(**{"class": "vibes"}), "class")

    def test_bad_incident_date(self):
        self.assert_refused(lambda c: c.update(incident_date="Sept 1"),
                            "incident_date")

    def test_bool_t_is_not_a_number(self):
        def mutate(c):
            c["events"][1] = {"t": True, "type": "progress", "bytes_written": 1}
        self.assert_refused(mutate, "events[1].t")

    def test_decreasing_t(self):
        def mutate(c):
            c["events"].append({"t": 5, "type": "progress", "bytes_written": 1})
        self.assert_refused(mutate, "events[2].t")

    def test_unknown_event_type(self):
        def mutate(c):
            c["events"].append({"t": 20, "type": "vibes"})
        self.assert_refused(mutate, "events[2].type")

    def test_probe_missing_cpu(self):
        def mutate(c):
            c["events"].append({"t": 20, "type": "probe", "probe_ok": True,
                                "last_write_age_s": 30, "stack_frames": None})
        self.assert_refused(mutate, "events[2].cpu_percent")

    def test_failed_probe_with_samples_refused(self):
        def mutate(c):
            c["events"].append({"t": 20, "type": "probe", "probe_ok": False,
                                "cpu_percent": 1.0})
        self.assert_refused(mutate, "events[2].cpu_percent")

    def test_first_event_must_open_at_zero(self):
        def mutate(c):
            c["events"][0] = {"t": 3, "type": "dispatch_open"}
        self.assert_refused(mutate, "events[0]")

    def test_empty_events(self):
        self.assert_refused(lambda c: c.update(events=[]), "events")

    def test_kill_expectation_needs_window(self):
        def mutate(c):
            c["expect"] = {"kill": True, "reason": "confirmed_wedge",
                           "evidence_keys": ["stack_frames"]}
        self.assert_refused(mutate, "expect.kill_window_s")

    def test_kill_window_must_be_ordered(self):
        def mutate(c):
            c["expect"] = {"kill": True, "reason": "confirmed_wedge",
                           "kill_window_s": [2200, 2000],
                           "evidence_keys": ["stack_frames"]}
        self.assert_refused(mutate, "expect.kill_window_s")

    def test_nokill_degraded_flag_must_be_bool(self):
        def mutate(c):
            c["expect"] = {"kill": False, "measurement_degraded": 1}
        self.assert_refused(mutate, "expect.measurement_degraded")


if __name__ == "__main__":
    unittest.main()
