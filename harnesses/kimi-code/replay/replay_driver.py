#!/usr/bin/env python3
"""Replay driver: feed a recorded incident timeline to a kill predicate (TOOL-039, #110).

Recorded signatures, simulated clock: the driver never sleeps and never reads
the wall clock. It walks the fixture's event list in order; after each event
it hands the predicate a window {now_s, dispatch, events-so-far} and records
the verdict. Decision points are exactly the recorded events — see the plan's
Review Focus for the sampling-bias caveat.

The window is READ-ONLY for the predicate. Python cannot enforce that cheaply;
the corpus contract is that decide() does not mutate its input.

Python 3.10, standard library only.
"""
import json
from pathlib import Path

from replay_schema import validate_case


class ExpectationFailure(AssertionError):
    """A replayed incident produced a decision the corpus forbids."""


class ReplayResult:
    def __init__(self, decisions):
        self.decisions = decisions  # [{"now_s": number, "verdict": dict}]


def load_case(path):
    """Read + strictly validate one corpus fixture."""
    return validate_case(json.loads(Path(path).read_text(encoding="utf-8")))


def run_case(case, decide):
    """Call decide(window) after every recorded event; collect verdicts."""
    seen = []
    decisions = []
    for ev in case["events"]:
        seen.append(ev)
        window = {"now_s": ev["t"], "dispatch": case["dispatch"],
                  "events": list(seen)}
        decisions.append({"now_s": ev["t"], "verdict": decide(window)})
    return ReplayResult(decisions)


def check_expectations(case, result):
    """Raise ExpectationFailure on the first violated expectation."""
    cid = case["case_id"]
    expect = case["expect"]
    kills = [d for d in result.decisions if d["verdict"].get("kill")]
    if expect["kill"]:
        if not kills:
            raise ExpectationFailure(
                f"{cid}: expected kill ({expect['reason']}), none fired")
        first = kills[0]
        lo, hi = expect["kill_window_s"]
        if not lo <= first["now_s"] <= hi:
            raise ExpectationFailure(
                f"{cid}: first kill at t={first['now_s']}, "
                f"outside expected [{lo}, {hi}]")
        reason = first["verdict"].get("reason")
        if reason != expect["reason"]:
            raise ExpectationFailure(
                f"{cid}: kill reason {reason!r}, expected {expect['reason']!r}")
        for key in expect.get("evidence_keys", []):
            if not first["verdict"].get("evidence", {}).get(key):
                raise ExpectationFailure(
                    f"{cid}: kill evidence missing non-empty {key!r}")
        return
    if kills:
        first = kills[0]
        raise ExpectationFailure(
            f"{cid}: forbidden kill at t={first['now_s']} "
            f"(reason={first['verdict'].get('reason')!r})")
    if "measurement_degraded" in expect:
        final = result.decisions[-1]["verdict"]
        if bool(final.get("measurement_degraded")) != expect["measurement_degraded"]:
            raise ExpectationFailure(
                f"{cid}: final measurement_degraded="
                f"{final.get('measurement_degraded')!r}, "
                f"expected {expect['measurement_degraded']!r}")
    if expect.get("backstop_crossed"):
        max_minutes = case["dispatch"].get("max_minutes")
        if max_minutes is None:
            raise ExpectationFailure(
                f"{cid}: backstop_crossed expectation needs dispatch.max_minutes")
        if not any(d["now_s"] > max_minutes * 60 for d in result.decisions):
            raise ExpectationFailure(
                f"{cid}: no decision past the {max_minutes} min wall-clock "
                f"backstop — fixture does not exercise the #108 regression")
