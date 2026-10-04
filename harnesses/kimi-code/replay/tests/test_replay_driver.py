#!/usr/bin/env python3
"""Driver tests: window shape, decision capture, expectation checks (TOOL-039, #110)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

from replay_driver import (  # noqa: E402
    ExpectationFailure, check_expectations, load_case, run_case)
from replay_schema import CaseSchemaError  # noqa: E402


def make_case(events, expect, dispatch=None):
    return {
        "schema_version": 1,
        "case_id": "2099-01-01-driver-unit",
        "incident_date": "2099-01-01",
        "class": "control_genuine_wedge" if expect["kill"] else "killed_after_success",
        "source_note": "driver unit fixture",
        "dispatch": dispatch or {"agent": "kimi-worker", "timeout_s": 1800},
        "events": events,
        "expect": expect,
    }


def no_kill(reason="insufficient_evidence"):
    return {"kill": False, "reason": reason,
            "measurement_degraded": False, "evidence": {}}


EVENTS = [
    {"t": 0, "type": "dispatch_open"},
    {"t": 10, "type": "progress", "bytes_written": 100},
    {"t": 20, "type": "probe", "probe_ok": True, "cpu_percent": 10.0,
     "last_write_age_s": 10, "stack_frames": None},
]


class TestRunCase(unittest.TestCase):
    def test_window_grows_prefix_per_event(self):
        seen = []

        def decide(window):
            seen.append((window["now_s"], window["dispatch"]["agent"],
                         [e["type"] for e in window["events"]]))
            return no_kill()

        result = run_case(make_case(EVENTS, {"kill": False}), decide)
        self.assertEqual([s[0] for s in seen], [0, 10, 20])
        self.assertEqual(seen[0][2], ["dispatch_open"])
        self.assertEqual(seen[1][2], ["dispatch_open", "progress"])
        self.assertEqual(seen[2][2], ["dispatch_open", "progress", "probe"])
        self.assertTrue(all(s[1] == "kimi-worker" for s in seen))
        self.assertEqual(len(result.decisions), 3)
        self.assertEqual([d["now_s"] for d in result.decisions], [0, 10, 20])

    def test_never_sleeps_and_calls_per_event(self):
        calls = [0]

        def decide(window):
            calls[0] += 1
            return no_kill()

        run_case(make_case(EVENTS, {"kill": False}), decide)
        self.assertEqual(calls[0], 3)


class TestCheckExpectationsNoKill(unittest.TestCase):
    def test_clean_no_kill_passes(self):
        case = make_case(EVENTS, {"kill": False})
        check_expectations(case, run_case(case, lambda w: no_kill()))

    def test_forbidden_kill_names_case_and_time(self):
        case = make_case(EVENTS, {"kill": False})

        def decide(window):
            if window["now_s"] >= 10:
                return {"kill": True, "reason": "confirmed_wedge",
                        "measurement_degraded": False, "evidence": {}}
            return no_kill()

        result = run_case(case, decide)
        with self.assertRaises(ExpectationFailure) as ctx:
            check_expectations(case, result)
        msg = str(ctx.exception)
        self.assertIn("2099-01-01-driver-unit", msg)
        self.assertIn("t=10", msg)

    def test_measurement_degraded_mismatch_fails(self):
        case = make_case(EVENTS, {"kill": False, "measurement_degraded": True})
        result = run_case(case, lambda w: no_kill())
        with self.assertRaises(ExpectationFailure):
            check_expectations(case, result)

    def test_measurement_degraded_match_passes(self):
        case = make_case(EVENTS, {"kill": False, "measurement_degraded": True})

        def decide(window):
            v = no_kill("measurement_degraded")
            v["measurement_degraded"] = True
            return v

        check_expectations(case, run_case(case, decide))

    def test_backstop_crossed_needs_decision_past_max_minutes(self):
        case = make_case(EVENTS, {"kill": False, "backstop_crossed": True},
                         dispatch={"agent": "kimi-worker", "timeout_s": 1800,
                                   "max_minutes": 45})
        result = run_case(case, lambda w: no_kill())
        with self.assertRaises(ExpectationFailure):
            check_expectations(case, result)  # last event t=20 < 2700

    def test_backstop_crossed_passes_with_late_decision(self):
        events = EVENTS + [{"t": 3000, "type": "probe", "probe_ok": True,
                            "cpu_percent": 4.0, "last_write_age_s": 60,
                            "stack_frames": None}]
        case = make_case(events, {"kill": False, "backstop_crossed": True},
                         dispatch={"agent": "kimi-worker", "timeout_s": 1800,
                                   "max_minutes": 45})
        check_expectations(case, run_case(case, lambda w: no_kill()))


class TestCheckExpectationsKill(unittest.TestCase):
    KILL_EXPECT = {"kill": True, "reason": "confirmed_wedge",
                   "kill_window_s": [15, 25], "evidence_keys": ["stack_frames"]}

    def killer(self, t_kill, reason="confirmed_wedge", evidence=None):
        def decide(window):
            if window["now_s"] >= t_kill:
                return {"kill": True, "reason": reason,
                        "measurement_degraded": False,
                        "evidence": evidence if evidence is not None
                        else {"stack_frames": ["a!b"]}}
            return no_kill()
        return decide

    def test_expected_kill_in_window_passes(self):
        case = make_case(EVENTS, dict(self.KILL_EXPECT))
        check_expectations(case, run_case(case, self.killer(20)))

    def test_no_kill_fails(self):
        case = make_case(EVENTS, dict(self.KILL_EXPECT))
        with self.assertRaises(ExpectationFailure):
            check_expectations(case, run_case(case, lambda w: no_kill()))

    def test_kill_outside_window_fails(self):
        case = make_case(EVENTS, dict(self.KILL_EXPECT))
        with self.assertRaises(ExpectationFailure):
            check_expectations(case, run_case(case, self.killer(10)))

    def test_wrong_reason_fails(self):
        case = make_case(EVENTS, dict(self.KILL_EXPECT))
        with self.assertRaises(ExpectationFailure):
            check_expectations(
                case, run_case(case, self.killer(20, reason="wall_clock")))

    def test_missing_evidence_key_fails(self):
        case = make_case(EVENTS, dict(self.KILL_EXPECT))
        with self.assertRaises(ExpectationFailure):
            check_expectations(
                case, run_case(case, self.killer(20, evidence={})))


class TestLoadCase(unittest.TestCase):
    def test_load_case_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps({"case_id": "x"}), encoding="utf-8")
            with self.assertRaises(CaseSchemaError):
                load_case(bad)


if __name__ == "__main__":
    unittest.main()
