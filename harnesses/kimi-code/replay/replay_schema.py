#!/usr/bin/env python3
"""Replay case-file schema: strict validation for the regression corpus (TOOL-039, #110).

Same doctrine as core/task_schema.py: strict types, known keys only, bools are
never numbers. A malformed fixture must fail loudly at load time — a silently
degraded replay is a false green on the exact incidents this corpus pins.

What the schema deliberately does NOT check: probe sample MAGNITUDES. Negative
CPU and impossible write ages are valid fixture content (class
monitor_measurement_failure); detecting them is the predicate's job (#109),
not the loader's.

Python 3.10, standard library only.
"""
from __future__ import annotations

import time

CASE_SCHEMA_VERSION = 1

KNOWN_CLASSES = frozenset((
    "completion_before_stall_kill",   # 2026-09-01 x2: success, then stall-killed
    "killed_after_success",           # 2026-09-12: killed 46s after success
    "monitor_measurement_failure",    # 2026-09-16/17: SMB-stat self-hang et al.
    "maxminutes_healthy_slow_run",    # 2026-08-25 x3: wall-clock kills of a live run
    "control_genuine_wedge",          # 2026-09-25 attempts 06/07: real wedge, must kill
))

EVENT_TYPES = frozenset((
    "dispatch_open", "progress", "probe", "completion_evidence",
))

COMPLETION_CHANNELS = frozenset(("envelope_completed", "progress_eof"))

_TOP_KEYS = frozenset(("schema_version", "case_id", "incident_date", "class",
                       "source_note", "dispatch", "events", "expect"))
_DISPATCH_KEYS = frozenset(("agent", "timeout_s", "max_minutes"))
_EXPECT_KEYS = frozenset(("kill", "reason", "kill_window_s", "evidence_keys",
                          "measurement_degraded", "backstop_crossed"))
_EVENT_KEYS = {
    "dispatch_open": frozenset(("t", "type")),
    "progress": frozenset(("t", "type", "bytes_written")),
    "probe": frozenset(("t", "type", "probe_ok", "cpu_percent",
                        "last_write_age_s", "stack_frames")),
    "completion_evidence": frozenset(("t", "type", "channel")),
}


class CaseSchemaError(Exception):
    """A replay fixture the harness must refuse. `field` names the offender."""

    def __init__(self, field, detail):
        super().__init__(f"{field}: {detail}")
        self.field = field
        self.detail = detail


def _require_str(value, field):
    if not isinstance(value, str) or not value.strip():
        raise CaseSchemaError(field, "must be a non-empty string")


def _require_bool(value, field):
    # bool is an int subclass; keep the A12 trap out of the corpus.
    if not isinstance(value, bool):
        raise CaseSchemaError(
            field, f"must be a boolean, not {type(value).__name__}: {value!r}")


def _require_num(value, field, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaseSchemaError(field, f"must be a number, not {value!r}")
    if minimum is not None and value < minimum:
        raise CaseSchemaError(field, f"must be >= {minimum}, not {value!r}")


def _reject_unknown(obj, known, field):
    extra = sorted(set(obj) - known)
    if extra:
        raise CaseSchemaError(field, f"unknown keys: {', '.join(extra)}")


def _validate_dispatch(dispatch):
    if not isinstance(dispatch, dict):
        raise CaseSchemaError("dispatch", "must be an object")
    _reject_unknown(dispatch, _DISPATCH_KEYS, "dispatch")
    _require_str(dispatch.get("agent"), "dispatch.agent")
    _require_num(dispatch.get("timeout_s"), "dispatch.timeout_s", minimum=0)
    if dispatch.get("max_minutes") is not None:
        _require_num(dispatch["max_minutes"], "dispatch.max_minutes", minimum=0)


def _validate_event(ev, i, prev_t):
    field = f"events[{i}]"
    if not isinstance(ev, dict):
        raise CaseSchemaError(field, "must be an object")
    etype = ev.get("type")
    if etype not in EVENT_TYPES:
        raise CaseSchemaError(f"{field}.type", f"unknown event type {etype!r}")
    _reject_unknown(ev, _EVENT_KEYS[etype], field)
    _require_num(ev.get("t"), f"{field}.t", minimum=0)
    if prev_t is not None and ev["t"] < prev_t:
        raise CaseSchemaError(f"{field}.t",
                              f"must be non-decreasing (previous {prev_t})")
    if etype == "progress":
        b = ev.get("bytes_written")
        if isinstance(b, bool) or not isinstance(b, int) or b < 0:
            raise CaseSchemaError(f"{field}.bytes_written",
                                  f"must be an integer >= 0, not {b!r}")
    elif etype == "probe":
        _require_bool(ev.get("probe_ok"), f"{field}.probe_ok")
        if ev["probe_ok"]:
            # Magnitudes unchecked ON PURPOSE: impossible samples are the
            # 2026-09-16/17 fixture content.
            _require_num(ev.get("cpu_percent"), f"{field}.cpu_percent")
            _require_num(ev.get("last_write_age_s"), f"{field}.last_write_age_s")
            frames = ev.get("stack_frames")
            if frames is not None:
                if (not isinstance(frames, list)
                        or not all(isinstance(f, str) and f for f in frames)):
                    raise CaseSchemaError(
                        f"{field}.stack_frames",
                        "must be null or an array of non-empty strings")
        else:
            for key in ("cpu_percent", "last_write_age_s", "stack_frames"):
                if key in ev:
                    raise CaseSchemaError(
                        f"{field}.{key}", "must be absent when probe_ok is false")
    elif etype == "completion_evidence":
        if ev.get("channel") not in COMPLETION_CHANNELS:
            raise CaseSchemaError(
                f"{field}.channel",
                f"must be one of {sorted(COMPLETION_CHANNELS)}")
    return ev["t"]


def _validate_expect(expect):
    if not isinstance(expect, dict):
        raise CaseSchemaError("expect", "must be an object")
    _reject_unknown(expect, _EXPECT_KEYS, "expect")
    _require_bool(expect.get("kill"), "expect.kill")
    if expect["kill"]:
        _require_str(expect.get("reason"), "expect.reason")
        window = expect.get("kill_window_s")
        if not isinstance(window, list) or len(window) != 2:
            raise CaseSchemaError("expect.kill_window_s", "must be [lo_s, hi_s]")
        _require_num(window[0], "expect.kill_window_s", minimum=0)
        _require_num(window[1], "expect.kill_window_s", minimum=0)
        if not window[0] < window[1]:
            raise CaseSchemaError("expect.kill_window_s", "lo must be < hi")
        keys = expect.get("evidence_keys")
        if (not isinstance(keys, list) or not keys
                or not all(isinstance(k, str) and k for k in keys)):
            raise CaseSchemaError("expect.evidence_keys",
                                  "must be a non-empty array of strings")
    else:
        for opt in ("measurement_degraded", "backstop_crossed"):
            if opt in expect:
                _require_bool(expect[opt], f"expect.{opt}")


def validate_case(case):
    """Validate a loaded case file; returns the case. Raises CaseSchemaError."""
    if not isinstance(case, dict):
        raise CaseSchemaError("case", "must be a JSON object")
    _reject_unknown(case, _TOP_KEYS, "case")
    version = case.get("schema_version")
    if isinstance(version, bool) or version != CASE_SCHEMA_VERSION:
        raise CaseSchemaError(
            "schema_version", f"must be {CASE_SCHEMA_VERSION}, not {version!r}")
    _require_str(case.get("case_id"), "case_id")
    date = case.get("incident_date")
    _require_str(date, "incident_date")
    try:
        time.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise CaseSchemaError("incident_date",
                              f"must be YYYY-MM-DD, not {date!r}")
    if case.get("class") not in KNOWN_CLASSES:
        raise CaseSchemaError(
            "class",
            f"must be one of {sorted(KNOWN_CLASSES)}, not {case.get('class')!r}")
    _require_str(case.get("source_note"), "source_note")
    _validate_dispatch(case.get("dispatch"))
    events = case.get("events")
    if not isinstance(events, list) or not events:
        raise CaseSchemaError("events", "must be a non-empty array")
    prev_t = None
    for i, ev in enumerate(events):
        prev_t = _validate_event(ev, i, prev_t)
    first = events[0]
    if first.get("type") != "dispatch_open" or first.get("t") != 0:
        raise CaseSchemaError("events[0]", "must be dispatch_open at t=0")
    _validate_expect(case.get("expect"))
    return case
