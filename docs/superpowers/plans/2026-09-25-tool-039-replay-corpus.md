# Regression Replay Corpus for the Monitoring Redesign Implementation Plan

> **Planning status resolution:** this document IS the planning that dachent/model_proctor#110 (TOOL-039) required. Executing it closes the ticket's planning phase; no separate plan artifact is owed.
>
> Executors: use the **subagent-driven-development** skill — one subagent per task, TDD per the numbered steps, exact commit commands as written.

## Goal

Deliver a committed, CI-running regression replay harness plus a 10-incident corpus such that (a) today the suite runs green against a reference predicate that encodes the target monitoring policy, and (b) when #106 (TOOL-035) ships the production kill predicate, the same corpus gates it — and thereby gates #105–#109 validation — with zero changes to corpus or driver.

## Architecture

A strictly-validated JSON corpus of recorded incident timelines lives under `harnesses/kimi-code/replay/corpus/`; a simulated-clock driver replays each timeline, calling a kill-decision function after every recorded event and checking the resulting decision stream against authored expectations. The decision function is pluggable: a reference predicate (the executable specification of the target policy) keeps the suite green at merge, and a bridge resolves the production predicate (`monitor:decide_kill`) once #106 lands, at which point the production replay suite unskips and becomes the acceptance gate. Fixtures are byte-stably regenerable via `gen_corpus.py` in the `evals/fixtures/gen_*.py` idiom, so the corpus is durable (AGENTS.md durability doctrine), diffable, and amendment-auditable.

## Tech Stack

Python 3.10, standard library only (AGENTS.md:115-116 — control-plane constraint). stdlib `unittest`, one discovery dir per suite, CI on `windows-latest` only (`.github/workflows/ci.yml`). JSON fixtures. No new dependencies.

## Spec

- Inventory: `mp_inventory_103-110.md` at the session root — §4 (monitoring/liveness), §5 (test infra), and the `#110 / TOOL-039` plan-feeding note. Verified against the worktree; corrections are listed in the Self-Review section.
- Ticket: GitHub `dachent/model_proctor` **#110** / TOOL-039 — regression replay corpus for the monitoring redesign; greenfield; gates #105–#109 validation.
- Canonical incidents to encode as replays (verbatim from the ticket):
  1. **2026-09-01 x2** — job succeeded 2–3 min before the stall kill → **no kill**.
  2. **2026-09-12** — killed 46 s after success → **no kill**.
  3. **2026-09-16/17** — monitor SMB-stat self-hang, impossible write-ages, negative CPU → **no kill + measurement_degraded**.
  4. **2026-08-25 x3** — MaxMinutes wall-clock kills of a healthy slow-VM run → **no kill**.
  5. **CONTROL 2026-09-25 attempts 06/07** — genuine COM wedge (busy CPU, zero writes 20–30 min, identical stack frames) → **kill WITH stack evidence**.

### Assumed interfaces the harness is written against (#105–#109 do not exist yet)

These are the contract. If a numbered ticket ships a different shape, updating `production_bridge.py` (and only it) is part of that ticket's acceptance.

- **AI-1 (#105, heartbeat/progress protocol):** payload progress reaches the monitor as `{type: "progress", t, bytes_written}` records; completion surfaces as `{type: "completion_evidence", channel: "envelope_completed"|"progress_eof"}`. The replay window's event list IS the shape #105's collector must produce.
- **AI-2 (#106, kill predicate):** `harnesses/kimi-code/delegate/monitor.py` exposes `decide_kill(window: dict) -> dict` — a PURE function (no I/O, no wall clock; `now_s` comes from the window). Verdict shape: `{"kill": bool, "reason": str, "measurement_degraded": bool, "evidence": dict}`. Overridable via `REPLAY_PREDICATE="module:function"` env var.
- **AI-3 (#107, single kill authority):** the corpus gates DECISIONS only; kill execution is out of replay scope and belongs to #107's own suite.
- **AI-4 (#108, timeout sizing):** wall-clock backstops (`timeout_s`, `max_minutes`) appear in `window["dispatch"]` but are not kill inputs while progress is fresh. Corpus class `maxminutes_healthy_slow_run` pins this.
- **AI-5 (#109, observer isolation):** a failed or self-inconsistent probe forces abstention: `kill=false, measurement_degraded=true`. Corpus class `monitor_measurement_failure` pins this.

## Global Constraints

- Python 3.10 stdlib only; no new packages (AGENTS.md:115-116).
- Tests: stdlib `unittest`, discoverable from the suite dir; suite registered in the `$suites` array in `.github/workflows/ci.yml` (array at lines 25-34; insert after `"harnesses/kimi-code/runner/tests",` line 29).
- **No git mutations without explicit user confirmation (AGENTS.md:121).** The commit commands below are exact; the executor (or user) must confirm before each one runs.
- `git add` with explicit paths only.
- `cascade/` is a frozen research artifact — do not import it, extend it, or cite it as authority (AGENTS.md:24-30).
- The harness is **decision-level, not process-level**: it never spawns subprocesses and does not reuse `fake_worker.py`/`fake_delegate.py` (those remain runner/cascade suite fixtures). The five incidents were decision errors, not transport errors, so replays feed observation windows into the predicate.
- All work happens in the `mp_wt` worktree; paths below are relative to it.

## Review Focus

The five input classes / failure modes the spec implies but **no task's tests exercise** — most likely to bite first. Each is pinned to its owning task or flagged as a reviewer action:

1. **Decision-point sampling bias.** The driver calls the predicate only at recorded events; production will call it on a timer. A predicate that needs a tick BETWEEN events (e.g., rate-of-change integrators) will behave differently under replay. Pinned: AI-2 requires purity over the window; `test_never_sleeps_and_calls_per_event` (Task 2). **Reviewer action at #106 integration: confirm `decide_kill` is pure.**
2. **Oracle overfitting (answer-key risk).** The reference predicate is authored by the same hand as the corpus; a lookup table would pass every test. Control: Task 3's boundary tests use ONLY synthetic windows absent from the corpus. **Reviewer action: grep `reference_predicate.py` for any `case_id`/class string — there must be none.**
3. **Fixture unit/magnitude errors.** The schema validates types, not magnitudes: a fixture authored in minutes instead of seconds, or a `t` scale that contradicts `source_note` (46 s, 2–3 min, 20–30 min, 45-min backstop), replays nonsense and can green the wrong predicate. Not machine-detectable. **Reviewer action: cross-check every `source_note` duration claim against the event `t` values in Task 4.**
4. **Stack-frame format drift.** Verdicts hinge on `list[str]` frame equality; if #106's production evidence emits dicts or normalized symbols, the control cases silently no-kill and the production suite goes red for a FORMAT reason, not a policy reason. Pinned: `test_confirmed_wedge_kills_with_stack_evidence` (Task 3) asserts exact frame content; `production_bridge.py` is the single adaptation point. **Reviewer action at #106: confirm frame format agreement before debugging verdicts.**
5. **Bridge import-time side effects.** Importing the production module executes its top level (the delegate idiom has module-level ctypes constants). The bridge catches `ImportError` only; an `OSError`/`AttributeError` at import time ERRORS the suite rather than skipping it. This is deliberate — a broken production import is a #106 defect worth red CI — **reviewer confirms the posture** rather than discovering it mid-incident.

---

## Task 1: Replay case schema + strict validator

### Files
- Create: `harnesses/kimi-code/replay/replay_schema.py`
- Test: `harnesses/kimi-code/replay/tests/test_replay_schema.py`

### Interfaces
Produces (consumed by Tasks 2, 4):
```text
CASE_SCHEMA_VERSION: int                      # == 1
KNOWN_CLASSES: frozenset[str]                 # the five incident classes
EVENT_TYPES: frozenset[str]                   # dispatch_open|progress|probe|completion_evidence
COMPLETION_CHANNELS: frozenset[str]           # envelope_completed|progress_eof
class CaseSchemaError(Exception): .field: str; .detail: str
def validate_case(case: dict) -> dict         # returns input; raises CaseSchemaError
```
Consumes: nothing (stdlib only).

### Steps

1. Write the failing test file `harnesses/kimi-code/replay/tests/test_replay_schema.py`:

```python
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
```

2. Run it, expect FAIL (module does not exist — collection error):
```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/replay/tests -p "test_replay_schema.py" -v
```
Expected: exit non-zero, `ModuleNotFoundError: No module named 'replay_schema'`.

3. Write the implementation `harnesses/kimi-code/replay/replay_schema.py`:

```python
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
```

4. Re-run, expect PASS (19 tests):
```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/replay/tests -p "test_replay_schema.py" -v
```
Expected: `Ran 19 tests ... OK`.

5. Commit (after explicit user confirmation per AGENTS.md:121):
```bash
cd mp_wt && git add harnesses/kimi-code/replay/replay_schema.py harnesses/kimi-code/replay/tests/test_replay_schema.py && git commit -m "Add replay case schema + strict validator (TOOL-039, #110)"
```

---

## Task 2: Simulated-clock replay driver

### Files
- Create: `harnesses/kimi-code/replay/replay_driver.py`
- Test: `harnesses/kimi-code/replay/tests/test_replay_driver.py`

### Interfaces
Consumes: `replay_schema.validate_case`, `replay_schema.CaseSchemaError` (Task 1).
Produces (consumed by Tasks 3–5):
```text
class ExpectationFailure(AssertionError)
class ReplayResult: .decisions: list[dict]   # [{"now_s": number, "verdict": dict}]
def load_case(path) -> dict                  # read + validate one fixture
def run_case(case: dict, decide: callable) -> ReplayResult
def check_expectations(case: dict, result: ReplayResult) -> None  # raises ExpectationFailure
```
`decide` is the AI-2 signature: `decide(window: dict) -> verdict dict`, where `window = {"now_s": number, "dispatch": dict, "events": list[dict]}` (events-so-far prefix, read-only to the callee).

### Steps

1. Write the failing test file `harnesses/kimi-code/replay/tests/test_replay_driver.py`:

```python
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
```

2. Run it, expect FAIL:
```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/replay/tests -p "test_replay_driver.py" -v
```
Expected: exit non-zero, `ModuleNotFoundError: No module named 'replay_driver'`.

3. Write the implementation `harnesses/kimi-code/replay/replay_driver.py`:

```python
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
```

4. Re-run, expect PASS (14 tests):
```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/replay/tests -p "test_replay_driver.py" -v
```
Expected: `Ran 14 tests ... OK`.

5. Commit:
```bash
cd mp_wt && git add harnesses/kimi-code/replay/replay_driver.py harnesses/kimi-code/replay/tests/test_replay_driver.py && git commit -m "Add simulated-clock replay driver + expectation checks (TOOL-039, #110)"
```

---

## Task 3: Reference kill predicate (executable spec for #106)

### Files
- Create: `harnesses/kimi-code/replay/reference_predicate.py`
- Test: `harnesses/kimi-code/replay/tests/test_reference_predicate.py`

### Interfaces
Consumes: the AI-2 window shape (Task 2). No imports beyond stdlib.
Produces (consumed by Task 4 corpus tests):
```text
STALL_AGE_FLOOR_S: int        # 600 — probe joins the suspect tail
WEDGE_AGE_CONFIRM_S: int      # 1500 — silence depth required to confirm
WEDGE_BUSY_CPU_PERCENT: float # 5.0 — busy CPU separates wedge from exited process
def reference_decide(window: dict) -> dict   # AI-2 verdict shape
```

### Steps

1. Write the failing test file `harnesses/kimi-code/replay/tests/test_reference_predicate.py`. Note: every window below is SYNTHETIC — none appears in the corpus. That is the anti-overfitting control (Review Focus #2):

```python
#!/usr/bin/env python3
"""Reference predicate boundary tests (TOOL-039, #110).

Synthetic windows only — none of these appear in the corpus. The predicate
must encode RULES, not answers.
"""
import sys
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

from reference_predicate import (  # noqa: E402
    WEDGE_AGE_CONFIRM_S, reference_decide)

COM = ["combase!CoWaitForMultipleHandles", "worker!com_call"]
IO = ["ntdll!NtReadFile", "worker!read"]
OPEN = {"t": 0, "type": "dispatch_open"}


def probe(t, cpu, age, stacks=None, ok=True):
    if not ok:
        return {"t": t, "type": "probe", "probe_ok": False}
    return {"t": t, "type": "probe", "probe_ok": True, "cpu_percent": cpu,
            "last_write_age_s": age, "stack_frames": stacks}


def prog(t, n=1024):
    return {"t": t, "type": "progress", "bytes_written": n}


def done(t):
    return {"t": t, "type": "completion_evidence",
            "channel": "envelope_completed"}


def window(events, dispatch=None):
    return {"now_s": events[-1]["t"],
            "dispatch": dispatch or {"agent": "a", "timeout_s": 1800},
            "events": events}


def wedged_probes(start=1200, step=300, count=4, final_age=WEDGE_AGE_CONFIRM_S):
    out = []
    for i in range(count):
        t = start + i * step
        age = final_age - (count - 1 - i) * step
        out.append(probe(t, 25.0, age, COM))
    return out


class TestCompletionSuppression(unittest.TestCase):
    def test_completion_suppresses_wedge_shaped_probes(self):
        # 2026-09-01/09-12 mechanism: post-success the process is idle, writes
        # silent, stacks identical — a wedge detector without a completion
        # check kills a finished job.
        w = window([OPEN, prog(300), done(900),
                    probe(1200, 0.2, 300, COM),
                    probe(1500, 0.1, 600, COM),
                    probe(1800, 0.1, 900, COM)])
        v = reference_decide(w)
        self.assertFalse(v["kill"])
        self.assertEqual(v["reason"], "completion_evidence")
        self.assertEqual(v["evidence"]["completion_t"], 900)
        self.assertFalse(v["measurement_degraded"])


class TestMeasurementAbstention(unittest.TestCase):
    def test_failed_probe_abstains(self):
        w = window([OPEN, prog(300), probe(600, 0, 0, ok=False)])
        v = reference_decide(w)
        self.assertFalse(v["kill"])
        self.assertTrue(v["measurement_degraded"])
        self.assertEqual(v["reason"], "measurement_degraded")

    def test_negative_cpu_abstains(self):
        w = window([OPEN, probe(300, -1.0, 30)])
        v = reference_decide(w)
        self.assertFalse(v["kill"])
        self.assertTrue(v["measurement_degraded"])

    def test_impossible_write_age_abstains(self):
        # Write age older than the dispatch itself (2026-09-17).
        w = window([OPEN, probe(300, 22.0, 999999)])
        v = reference_decide(w)
        self.assertFalse(v["kill"])
        self.assertTrue(v["measurement_degraded"])


class TestWedgeBoundaries(unittest.TestCase):
    def test_confirmed_wedge_kills_with_stack_evidence(self):
        w = window([OPEN, prog(300)] + wedged_probes())
        v = reference_decide(w)
        self.assertTrue(v["kill"])
        self.assertEqual(v["reason"], "confirmed_wedge")
        self.assertEqual(v["evidence"]["stack_frames"], COM)
        self.assertGreaterEqual(v["evidence"]["confirming_probes"], 2)

    def test_identical_stacks_required(self):
        probes = wedged_probes()
        probes[-1]["stack_frames"] = IO  # stack moved — not wedged
        w = window([OPEN, prog(300)] + probes)
        self.assertFalse(reference_decide(w)["kill"])

    def test_stack_evidence_required(self):
        probes = wedged_probes()
        for p in probes:
            p["stack_frames"] = None
        w = window([OPEN, prog(300)] + probes)
        self.assertFalse(reference_decide(w)["kill"])

    def test_silence_depth_required(self):
        probes = wedged_probes(final_age=WEDGE_AGE_CONFIRM_S - 300)
        w = window([OPEN, prog(300)] + probes)
        self.assertFalse(reference_decide(w)["kill"])

    def test_busy_cpu_required(self):
        # Silent writes + identical stacks + IDLE cpu = exited-but-unreaped,
        # not a wedge.
        probes = [probe(1200 + i * 300, 0.3, 600 + i * 300, COM)
                  for i in range(4)]
        w = window([OPEN, prog(300)] + probes)
        self.assertFalse(reference_decide(w)["kill"])

    def test_fresh_progress_suppresses_stale_looking_probes(self):
        # #105: the progress channel is primary evidence; a lagging probe
        # sample must not outvote fresh writes.
        w = window([OPEN, prog(300), probe(600, 25.0, 590), prog(900)])
        v = reference_decide(w)
        self.assertFalse(v["kill"])
        self.assertEqual(v["reason"], "progress_fresh")

    def test_wall_clock_is_not_a_kill_input(self):
        # 2026-08-25 / #108: past the MaxMinutes backstop with fresh progress
        # the only legal answer is no kill.
        w = window([OPEN, prog(3500), probe(3600, 4.0, 100)],
                   dispatch={"agent": "a", "timeout_s": 1800, "max_minutes": 45})
        self.assertFalse(reference_decide(w)["kill"])


if __name__ == "__main__":
    unittest.main()
```

2. Run it, expect FAIL:
```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/replay/tests -p "test_reference_predicate.py" -v
```
Expected: exit non-zero, `ModuleNotFoundError: No module named 'reference_predicate'`.

3. Write the implementation `harnesses/kimi-code/replay/reference_predicate.py`:

```python
#!/usr/bin/env python3
"""Reference kill predicate — the executable specification for #106 (TOOL-039, #110).

NOT production code. This module encodes, as minimal ordered rules, the
verdicts the five recorded incident classes must produce under the monitoring
redesign. When #106 ships the real predicate (assumed interface AI-2:
harnesses/kimi-code/delegate/monitor.py::decide_kill), production_bridge
replays the same corpus against it; this module stays as the oracle the corpus
was authored against, and its boundary tests are the anti-overfitting control.

Rule order is load-bearing: completion evidence suppresses everything (a
finished job looks exactly like a wedge to write-age/stack rules), and
degraded measurements force abstention before any kill arithmetic. The wall
clock is never read: window["dispatch"] budgets are carried, not consulted.

Python 3.10, standard library only.
"""

STALL_AGE_FLOOR_S = 600        # writes silent this long -> probe joins suspect tail
WEDGE_AGE_CONFIRM_S = 1500     # silence depth required to confirm a wedge
WEDGE_BUSY_CPU_PERCENT = 5.0   # busy CPU separates a wedge from an exited process


def _latest_probe(events):
    for e in reversed(events):
        if e["type"] == "probe":
            return e
    return None


def _sample_consistent(probe, now_s):
    # 2026-09-16/17: negative CPU, negative write age, or a write age older
    # than the dispatch itself are measurement failures, not evidence.
    if probe["cpu_percent"] < 0:
        return False
    age = probe["last_write_age_s"]
    return 0 <= age <= now_s + 60


def reference_decide(window):
    """Assumed #106 interface (AI-2): window -> verdict dict. Pure; no I/O."""
    events = window["events"]
    now_s = window["now_s"]

    probe = _latest_probe(events)
    degraded = bool(
        probe is not None
        and (not probe["probe_ok"] or not _sample_consistent(probe, now_s)))

    for e in events:
        if e["type"] == "completion_evidence":
            # 2026-09-01/09-12: success evidence suppresses every kill path.
            return {"kill": False, "reason": "completion_evidence",
                    "measurement_degraded": degraded,
                    "evidence": {"completion_t": e["t"],
                                 "channel": e["channel"]}}

    if degraded:
        # #109: a monitor that cannot trust its own samples abstains.
        return {"kill": False, "reason": "measurement_degraded",
                "measurement_degraded": True,
                "evidence": {"probe_t": probe["t"],
                             "probe_ok": probe["probe_ok"]}}

    progress_ts = [e["t"] for e in events if e["type"] == "progress"]
    if progress_ts and now_s - progress_ts[-1] < WEDGE_AGE_CONFIRM_S:
        # #105: the progress channel is primary evidence (2026-08-25).
        return {"kill": False, "reason": "progress_fresh",
                "measurement_degraded": False,
                "evidence": {"last_progress_t": progress_ts[-1]}}

    probes = [e for e in events if e["type"] == "probe" and e["probe_ok"]]
    tail = []
    for p in reversed(probes):
        if (p["cpu_percent"] >= WEDGE_BUSY_CPU_PERCENT
                and p["last_write_age_s"] >= STALL_AGE_FLOOR_S):
            tail.append(p)
        else:
            break
    tail.reverse()
    if (len(tail) >= 2
            and tail[-1]["last_write_age_s"] >= WEDGE_AGE_CONFIRM_S
            and tail[-1]["stack_frames"]
            and tail[-1]["stack_frames"] == tail[-2]["stack_frames"]):
        # 2026-09-25 attempts 06/07: busy CPU + silent writes + identical
        # consecutive stacks = confirmed wedge; kill WITH the stack evidence.
        return {"kill": True, "reason": "confirmed_wedge",
                "measurement_degraded": False,
                "evidence": {"stack_frames": tail[-1]["stack_frames"],
                             "silent_writes_s": tail[-1]["last_write_age_s"],
                             "confirming_probes": len(tail)}}

    return {"kill": False, "reason": "insufficient_evidence",
            "measurement_degraded": False, "evidence": {}}
```

4. Re-run, expect PASS (11 tests):
```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/replay/tests -p "test_reference_predicate.py" -v
```
Expected: `Ran 11 tests ... OK`.

5. Commit:
```bash
cd mp_wt && git add harnesses/kimi-code/replay/reference_predicate.py harnesses/kimi-code/replay/tests/test_reference_predicate.py && git commit -m "Add reference kill predicate as executable spec for #106 (TOOL-039, #110)"
```

---

## Task 4: Corpus generator + 10 committed incident fixtures

### Files
- Create: `harnesses/kimi-code/replay/gen_corpus.py`
- Create (generated, then committed): `harnesses/kimi-code/replay/corpus/*.json` — 10 fixtures
- Test: `harnesses/kimi-code/replay/tests/test_corpus.py`

### Interfaces
Consumes: `replay_schema.validate_case` (Task 1); `reference_predicate.reference_decide`, `replay_driver.{load_case, run_case, check_expectations}` (Tasks 2–3) in tests.
Produces:
```text
INCIDENTS: list[dict]        # the 10 case dicts, pre-validation
def main(out_dir) -> None    # writes <case_id>.json per incident, LF bytes
```
Corpus fixture filenames (exact):
```
2026-08-25-maxminutes-slow-vm-kill-1.json
2026-08-25-maxminutes-slow-vm-kill-2.json
2026-08-25-maxminutes-slow-vm-kill-3.json
2026-09-01-completion-stall-kill-a.json
2026-09-01-completion-stall-kill-b.json
2026-09-12-killed-46s-after-success.json
2026-09-16-monitor-smb-self-hang.json
2026-09-17-impossible-write-ages-negative-cpu.json
2026-09-25-com-wedge-attempt06.json
2026-09-25-com-wedge-attempt07.json
```

### Steps

1. Write the failing test file `harnesses/kimi-code/replay/tests/test_corpus.py`:

```python
#!/usr/bin/env python3
"""Corpus tests: validity, reference replay, byte-stable regeneration (TOOL-039, #110)."""
import sys
import tempfile
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

import gen_corpus  # noqa: E402
from reference_predicate import reference_decide  # noqa: E402
from replay_driver import check_expectations, load_case, run_case  # noqa: E402
from replay_schema import KNOWN_CLASSES  # noqa: E402

CORPUS = _DIR / "corpus"
EXPECTED_CASES = 10
EXPECTED_KILLS = 2  # only the 2026-09-25 control attempts may kill


def all_cases():
    return sorted(CORPUS.glob("*.json"))


class TestCorpusValidity(unittest.TestCase):
    def test_every_fixture_validates_and_matches_filename(self):
        paths = all_cases()
        self.assertEqual(len(paths), EXPECTED_CASES,
                         f"corpus should hold {EXPECTED_CASES} incidents")
        for path in paths:
            with self.subTest(file=path.name):
                case = load_case(path)
                self.assertEqual(path.name, case["case_id"] + ".json")

    def test_all_five_classes_present(self):
        classes = {load_case(p)["class"] for p in all_cases()}
        self.assertEqual(classes, KNOWN_CLASSES)

    def test_only_control_cases_expect_kill(self):
        kills = [load_case(p)["case_id"] for p in all_cases()
                 if load_case(p)["expect"]["kill"]]
        self.assertEqual(len(kills), EXPECTED_KILLS)
        for cid in kills:
            self.assertIn("2026-09-25", cid)


class TestReferenceReplay(unittest.TestCase):
    def test_all_cases_match_authored_verdicts(self):
        for path in all_cases():
            case = load_case(path)
            with self.subTest(case_id=case["case_id"]):
                check_expectations(case, run_case(case, reference_decide))


class TestRegeneration(unittest.TestCase):
    def test_corpus_regeneration_is_byte_stable(self):
        # core.autocrlf is workstation-dependent (true on the authoring
        # machine, no .gitattributes in-repo): compare with line endings
        # normalized on the checked-out side only. The generator itself
        # always writes LF bytes.
        with tempfile.TemporaryDirectory() as tmp:
            gen_corpus.main(tmp)
            for path in all_cases():
                regen = (Path(tmp) / path.name).read_bytes()
                committed = path.read_bytes().replace(b"\r\n", b"\n")
                self.assertEqual(
                    regen, committed,
                    f"{path.name}: regenerate with gen_corpus.py and commit "
                    f"the result")


if __name__ == "__main__":
    unittest.main()
```

2. Run it, expect FAIL:
```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/replay/tests -p "test_corpus.py" -v
```
Expected: exit non-zero, `ModuleNotFoundError: No module named 'gen_corpus'`.

3. Write the implementation `harnesses/kimi-code/replay/gen_corpus.py`. The ten incident definitions below are the corpus content — the `source_note` fields record what the original production traces showed (the originals live outside the repo per the durability doctrine; these reconstructions are the durable artifact):

```python
#!/usr/bin/env python3
"""Deterministic generator for the replay corpus (TOOL-039, #110).

The recorded production failure signatures live outside the repo (durability
doctrine, AGENTS.md); what is committed under corpus/ is the reconstruction of
each incident's decision-relevant trace, regenerated byte-identically by this
script. Idiom follows evals/fixtures/gen_*.py: `python gen_corpus.py <out_dir>`.

Every incident is validated against replay_schema before being written — a
generator bug fails here, not in CI.

Python 3.10, standard library only.
"""
import json
import sys
from pathlib import Path

from replay_schema import validate_case

COM_STACK = ["combase!CoWaitForMultipleHandles",
             "msxml6!Document::load",
             "python310!PyEval_EvalFrameDefault"]
IDLE_STACK = ["ntdll!NtWaitForSingleObject",
              "python310!PyEval_EvalFrameDefault"]
IO_STACK = ["ntdll!NtReadFile", "python310!PyEval_EvalFrameDefault"]


def _open():
    return {"t": 0, "type": "dispatch_open"}


def _prog(t, n):
    return {"t": t, "type": "progress", "bytes_written": n}


def _probe(t, cpu, age, stacks=None):
    return {"t": t, "type": "probe", "probe_ok": True, "cpu_percent": cpu,
            "last_write_age_s": age, "stack_frames": stacks}


def _probe_failed(t):
    # 2026-09-16/17: the monitor's own probe never completed (SMB-stat hang).
    return {"t": t, "type": "probe", "probe_ok": False}


def _done(t, channel="envelope_completed"):
    return {"t": t, "type": "completion_evidence", "channel": channel}


def _case(case_id, date, cls, note, events, expect, timeout_s=1800,
          max_minutes=None):
    dispatch = {"agent": "kimi-worker", "timeout_s": timeout_s}
    if max_minutes is not None:
        dispatch["max_minutes"] = max_minutes
    return {"schema_version": 1, "case_id": case_id, "incident_date": date,
            "class": cls, "source_note": note, "dispatch": dispatch,
            "events": events, "expect": expect}


INCIDENTS = [
    # ── 2026-09-01 x2: succeeded 2-3 min before the stall kill ──────────
    _case(
        "2026-09-01-completion-stall-kill-a", "2026-09-01",
        "completion_before_stall_kill",
        "Job A succeeded at t=1740; the stall heuristic killed it at ~t=1920, "
        "3 min after success. Post-completion the idle worker reads as a wedge "
        "(silent writes, idle stack) to any predicate that does not check "
        "completion evidence first.",
        [_open(),
         _prog(120, 4096), _probe(300, 18.0, 60),
         _prog(600, 32768), _probe(900, 22.5, 300),
         _prog(1200, 131072), _probe(1500, 15.1, 300),
         _prog(1740, 262144), _done(1740),
         _probe(1800, 0.4, 60, IDLE_STACK),
         _probe(1920, 0.2, 180, IDLE_STACK)],
        {"kill": False}),
    _case(
        "2026-09-01-completion-stall-kill-b", "2026-09-01",
        "completion_before_stall_kill",
        "Job B the same night: success at t=2280, stall kill at ~t=2400 — "
        "2 min after success.",
        [_open(),
         _prog(300, 8192), _probe(600, 19.4, 300),
         _prog(900, 65536), _probe(1200, 21.0, 300),
         _prog(1500, 196608), _probe(1800, 16.8, 300),
         _prog(2100, 229376), _done(2280),
         _probe(2340, 0.6, 60, IDLE_STACK),
         _probe(2400, 0.3, 120, IDLE_STACK)],
        {"kill": False}),
    # ── 2026-09-12: killed 46 s after success ───────────────────────────
    _case(
        "2026-09-12-killed-46s-after-success", "2026-09-12",
        "killed_after_success",
        "Envelope completed at t=1500; the kill landed 46 s later. Any "
        "post-completion reaction window shorter than the kill path is fatal.",
        [_open(),
         _prog(300, 4096), _probe(600, 17.7, 300),
         _prog(900, 45056), _probe(1200, 20.3, 300),
         _prog(1500, 90112), _done(1500),
         _probe(1546, 0.1, 46, IDLE_STACK)],
        {"kill": False}),
    # ── 2026-09-16/17: monitor measurement failure → abstain ────────────
    _case(
        "2026-09-16-monitor-smb-self-hang", "2026-09-16",
        "monitor_measurement_failure",
        "Monitor wedged stat-ing the SMB-hosted run dir; the CPU counter "
        "returned -1 at t=900 and the t=1500 probe never completed. The "
        "worker kept writing throughout — every kill input was a measurement "
        "artifact.",
        [_open(),
         _prog(300, 4096), _probe(300, 14.2, 45),
         _prog(600, 20480), _probe(900, -1.0, -1),
         _prog(1200, 45056), _probe_failed(1500),
         _prog(1800, 90112)],
        {"kill": False, "measurement_degraded": True}),
    _case(
        "2026-09-17-impossible-write-ages-negative-cpu", "2026-09-17",
        "monitor_measurement_failure",
        "Write age 999999 s at t=960 — older than the dispatch itself — and "
        "CPU -3.5%: physically impossible samples must read as "
        "measurement_degraded, never as a stalled worker.",
        [_open(),
         _prog(240, 4096), _probe(240, 22.0, 30),
         _prog(480, 16384), _probe(960, -3.5, 999999),
         _prog(1440, 32768), _probe_failed(1680)],
        {"kill": False, "measurement_degraded": True}),
    # ── 2026-08-25 x3: MaxMinutes wall-clock kills of a healthy run ─────
    _case(
        "2026-08-25-maxminutes-slow-vm-kill-1", "2026-08-25",
        "maxminutes_healthy_slow_run",
        "First of three MaxMinutes wall-clock kills of one healthy run on a "
        "slow VM. Progress stayed fresh through the 45-minute backstop; the "
        "wall clock was the only kill input.",
        [_open(),
         _prog(600, 8192), _probe(600, 3.5, 220),
         _prog(1200, 16384), _probe(1200, 4.1, 300),
         _prog(1800, 24576), _probe(1800, 5.9, 240),
         _prog(2400, 32768), _probe(2400, 4.4, 480),
         _prog(3000, 40960), _probe(3000, 6.8, 610),
         _probe(3300, 5.2, 300)],
        {"kill": False, "backstop_crossed": True}, max_minutes=45),
    _case(
        "2026-08-25-maxminutes-slow-vm-kill-2", "2026-08-25",
        "maxminutes_healthy_slow_run",
        "Second MaxMinutes kill after the restart; slower still — write ages "
        "up to 700 s — and still live at every checkpoint.",
        [_open(),
         _prog(600, 4096), _probe(600, 2.8, 400),
         _prog(1500, 12288), _probe(1500, 6.1, 620),
         _prog(2100, 20480), _probe(2100, 5.5, 700),
         _prog(2700, 28672), _probe(2700, 3.9, 450),
         _prog(3300, 36864), _probe(3300, 6.4, 650),
         _probe(3600, 4.7, 300)],
        {"kill": False, "backstop_crossed": True}, max_minutes=45),
    _case(
        "2026-08-25-maxminutes-slow-vm-kill-3", "2026-08-25",
        "maxminutes_healthy_slow_run",
        "Third kill; the worker sat in the same IO read stack across probes. "
        "Identical stacks with fresh writes are not a wedge.",
        [_open(),
         _prog(600, 4096), _probe(600, 4.9, 310, IO_STACK),
         _prog(1200, 10240), _probe(1200, 5.3, 420, IO_STACK),
         _prog(1800, 16384), _probe(1800, 5.0, 480, IO_STACK),
         _prog(2400, 22528), _probe(2400, 6.2, 540, IO_STACK),
         _prog(3000, 28672), _probe(3000, 5.8, 590, IO_STACK),
         _probe(3600, 4.2, 480, IO_STACK)],
        {"kill": False, "backstop_crossed": True}, max_minutes=45),
    # ── CONTROL 2026-09-25 attempts 06/07: genuine COM wedge → kill ─────
    _case(
        "2026-09-25-com-wedge-attempt06", "2026-09-25",
        "control_genuine_wedge",
        "CONTROL. Attempt 06: genuine COM wedge — busy CPU, zero writes for "
        "25 min (last write t=600, kill expected t=2100), identical COM stack "
        "frames across probes. Must kill WITH stack evidence.",
        [_open(),
         _prog(300, 4096), _prog(600, 10240),
         _probe(900, 24.0, 300, COM_STACK),
         _probe(1200, 26.5, 600, COM_STACK),
         _probe(1500, 31.2, 900, COM_STACK),
         _probe(1800, 28.4, 1200, COM_STACK),
         _probe(2100, 33.1, 1500, COM_STACK)],
        {"kill": True, "reason": "confirmed_wedge",
         "kill_window_s": [2000, 2200], "evidence_keys": ["stack_frames"]}),
    _case(
        "2026-09-25-com-wedge-attempt07", "2026-09-25",
        "control_genuine_wedge",
        "CONTROL. Attempt 07: same wedge signature — writes silent from "
        "t=900, kill expected at t=2400 (25 min of silence).",
        [_open(),
         _prog(300, 4096), _prog(900, 12288),
         _probe(1200, 21.0, 300, COM_STACK),
         _probe(1500, 25.5, 600, COM_STACK),
         _probe(1800, 29.8, 900, COM_STACK),
         _probe(2100, 27.3, 1200, COM_STACK),
         _probe(2400, 30.6, 1500, COM_STACK)],
        {"kill": True, "reason": "confirmed_wedge",
         "kill_window_s": [2300, 2500], "evidence_keys": ["stack_frames"]}),
]


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for case in INCIDENTS:
        validate_case(case)
        payload = (json.dumps(case, indent=2, sort_keys=True) + "\n"
                   ).encode("utf-8")
        # LF bytes explicitly: byte-stability must survive core.autocrlf.
        (out / f"{case['case_id']}.json").write_bytes(payload)


if __name__ == "__main__":
    main(sys.argv[1])
```

4. Generate the corpus and run the tests, expect PASS (5 tests):
```bash
cd mp_wt && python harnesses/kimi-code/replay/gen_corpus.py harnesses/kimi-code/replay/corpus
ls harnesses/kimi-code/replay/corpus   # expect exactly the 10 filenames above
python -m unittest discover -s harnesses/kimi-code/replay/tests -p "test_corpus.py" -v
```
Expected: `Ran 5 tests ... OK`. If `TestReferenceReplay` fails, the bug is in the generator's numbers or the predicate's rules — do NOT edit expectations to force green; the corpus and predicate must agree by construction.

5. Commit:
```bash
cd mp_wt && git add harnesses/kimi-code/replay/gen_corpus.py harnesses/kimi-code/replay/corpus harnesses/kimi-code/replay/tests/test_corpus.py && git commit -m "Add 10-incident regression replay corpus, five classes (TOOL-039, #110)"
```

---

## Task 5: Production predicate bridge + gated production replay

### Files
- Create: `harnesses/kimi-code/replay/production_bridge.py`
- Test: `harnesses/kimi-code/replay/tests/test_production_bridge.py`
- Test: `harnesses/kimi-code/replay/tests/test_corpus_production.py`

### Interfaces
Consumes: AI-2 (`monitor:decide_kill` in `harnesses/kimi-code/delegate/`, overridable via `REPLAY_PREDICATE` env); `replay_driver.{load_case, run_case, check_expectations}` (Task 2).
Produces:
```text
DEFAULT_SPEC: str   # "monitor:decide_kill"
def load_production_decide(environ: dict | None = None) -> callable | None
    # None  -> module/function not importable yet (pre-#106): production suite skips
    # callable -> production suite runs the corpus against it
    # ValueError -> malformed REPLAY_PREDICATE spec
```

### Steps

1. Write the failing test files.

`harnesses/kimi-code/replay/tests/test_production_bridge.py`:

```python
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
```

`harnesses/kimi-code/replay/tests/test_corpus_production.py`:

```python
#!/usr/bin/env python3
"""Production replay: the corpus gates #105-#109 (TOOL-039, #110).

Skips wholesale until #106's predicate is importable through
production_bridge. Once it resolves, this suite is the acceptance gate: every
recorded incident must produce its authored verdict against PRODUCTION code.
"""
import sys
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

from production_bridge import load_production_decide  # noqa: E402
from replay_driver import check_expectations, load_case, run_case  # noqa: E402

CORPUS = _DIR / "corpus"
DECIDE = load_production_decide()


@unittest.skipUnless(DECIDE is not None,
                     "production kill predicate absent — lands with #106 (TOOL-035)")
class TestProductionReplay(unittest.TestCase):
    def test_all_cases_match_authored_verdicts(self):
        for path in sorted(CORPUS.glob("*.json")):
            case = load_case(path)
            with self.subTest(case_id=case["case_id"]):
                check_expectations(case, run_case(case, DECIDE))


if __name__ == "__main__":
    unittest.main()
```

2. Run them, expect FAIL:
```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/replay/tests -p "test_production_bridge.py" -v
```
Expected: exit non-zero, `ModuleNotFoundError: No module named 'production_bridge'`.

3. Write the implementation `harnesses/kimi-code/replay/production_bridge.py`:

```python
#!/usr/bin/env python3
"""Bridge to the production kill predicate (#106) for corpus replay (TOOL-039, #110).

Assumed interface (AI-2): #106 ships harnesses/kimi-code/delegate/monitor.py
exposing decide_kill(window) -> verdict dict. Until then this resolves to
None and the production replay suite skips. Override with
REPLAY_PREDICATE="module:function" for local integration experiments.

Only ImportError means "absent": a production module that EXISTS but dies at
import (OSError, AttributeError from its ctypes top level) propagates and
turns the suite red on purpose — a broken production import is a #106 defect,
not a replay skip (see plan Review Focus #5).

Python 3.10, standard library only.
"""
import importlib
import os
import sys
from pathlib import Path

DEFAULT_SPEC = "monitor:decide_kill"


def load_production_decide(environ=None):
    env = os.environ if environ is None else environ
    spec = env.get("REPLAY_PREDICATE", DEFAULT_SPEC)
    module_name, sep, func_name = spec.partition(":")
    if not sep or not module_name or not func_name:
        raise ValueError(
            f"REPLAY_PREDICATE must be 'module:function', got {spec!r}")
    delegate_dir = Path(__file__).resolve().parents[1] / "delegate"
    if str(delegate_dir) not in sys.path:
        sys.path.insert(0, str(delegate_dir))
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    return getattr(module, func_name, None)
```

4. Re-run, expect PASS (4 tests) and the production suite SKIP:
```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/replay/tests -p "test_production_bridge.py" -v
python -m unittest discover -s harnesses/kimi-code/replay/tests -p "test_corpus_production.py" -v
```
Expected: bridge `Ran 4 tests ... OK`; production `Ran 1 test ... OK (skipped=1)` with reason "production kill predicate absent — lands with #106 (TOOL-035)".

5. Commit:
```bash
cd mp_wt && git add harnesses/kimi-code/replay/production_bridge.py harnesses/kimi-code/replay/tests/test_production_bridge.py harnesses/kimi-code/replay/tests/test_corpus_production.py && git commit -m "Add production predicate bridge gating #105-#109 on the corpus (TOOL-039, #110)"
```

---

## Task 6: CI wiring + documentation

### Files
- Modify: `.github/workflows/ci.yml` (one line — add the suite to `$suites`)
- Modify: `AGENTS.md` (Layout bullet + Commands line)
- Create: `harnesses/kimi-code/replay/README.md`

### Interfaces
Consumes: everything from Tasks 1–5. Produces: CI coverage for the new suite; repo documentation matching reality.

### Steps

1. Evidence-first check — run the CI suite loop locally BEFORE the edit and observe the replay suite is not covered (it simply isn't in the list):
```bash
cd mp_wt && grep -n "harnesses/kimi-code" .github/workflows/ci.yml
```
Expected: lines for `delegate/tests` (26), `runner/tests` (29) — and NO `replay/tests` entry.

2. Edit `.github/workflows/ci.yml`: in the `$suites` array, insert after line 29 (`"harnesses/kimi-code/runner/tests",`) this line, matching the existing indentation and quoting:
```
            "harnesses/kimi-code/replay/tests",
```

3. Edit `AGENTS.md`:
   - In the Layout section, after the `harnesses/kimi-code/runner/` bullet, insert:
     ```
     - `harnesses/kimi-code/replay/` — regression replay corpus for the monitoring redesign
       (TOOL-039, #110): recorded incident signatures (`corpus/`, regenerate with `gen_corpus.py`)
       replayed on a simulated clock against the kill predicate — the reference oracle
       (`reference_predicate.py`) today, the #106 production predicate via `production_bridge.py`
       once it exists. Tests in `harnesses/kimi-code/replay/tests/`.
     ```
   - In the Commands section, after the runner smoke suite line, insert:
     ```
     - Replay corpus (TOOL-039): `python -m unittest discover -s harnesses/kimi-code/replay/tests -v`
     ```

4. Write `harnesses/kimi-code/replay/README.md`:

```markdown
# Regression replay corpus (TOOL-039, #110)

Recorded production failure signatures, replayed on a simulated clock against
the monitoring kill predicate. The corpus exists so the monitoring redesign
(#105–#109) cannot reintroduce the five incident classes it was designed to
fix — and cannot soften the one control case it must still catch.

## Layout

- `replay_schema.py` — strict case-file validation (task_schema doctrine).
- `replay_driver.py` — simulated-clock replay: `decide(window)` after every
  recorded event, then expectation checks. Never sleeps, never reads the wall
  clock.
- `reference_predicate.py` — the executable specification of the target
  policy; the oracle this corpus was authored against. NOT production code.
- `production_bridge.py` — resolves the #106 production predicate
  (`monitor:decide_kill`, override with `REPLAY_PREDICATE="module:function"`).
  Returns None until #106 lands; the production suite skips until then.
- `gen_corpus.py` — deterministic corpus generator (`python gen_corpus.py
  <out_dir>`); `corpus/` is its byte-stable committed output.
- `corpus/` — the 10 recorded incidents across 5 classes.
- `tests/` — the unittest suite (in CI's `$suites` array).

## The five incident classes

| Class | Incidents | Expected verdict |
|---|---|---|
| `completion_before_stall_kill` | 2026-09-01 x2 | no kill (completion evidence suppresses) |
| `killed_after_success` | 2026-09-12 (46 s) | no kill |
| `monitor_measurement_failure` | 2026-09-16, 2026-09-17 | no kill + `measurement_degraded` |
| `maxminutes_healthy_slow_run` | 2026-08-25 x3 | no kill past the wall-clock backstop |
| `control_genuine_wedge` | 2026-09-25 attempts 06/07 | kill `confirmed_wedge` WITH stack evidence |

## Adding an incident

1. Add the incident to `INCIDENTS` in `gen_corpus.py` with a `source_note`
   stating what the original trace showed and where it lives.
2. `python harnesses/kimi-code/replay/gen_corpus.py harnesses/kimi-code/replay/corpus`
3. Run the suite; commit the generator change and the new fixture together.
   `test_corpus_regeneration_is_byte_stable` enforces corpus == generator
   output, so hand-edited fixtures fail.

The original production traces live outside the repo (durability doctrine);
the committed fixtures are the durable, reviewable reconstruction.
```

5. Verify the full new suite green, then the whole CI matrix locally:
```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/replay/tests -v
```
Expected: `Ran 54 tests ... OK (skipped=1)` — 19 schema + 14 driver + 11 predicate + 5 corpus + 4 bridge + 1 production (skipped).

```bash
cd mp_wt && for s in harnesses/kimi-code/delegate/tests harnesses/codex/delegate/tests harnesses/codex/skill/model-proctor/tests harnesses/kimi-code/runner/tests harnesses/kimi-code/replay/tests core/tests scripts/tests evals/tests harnesses/zcode/tests; do python -m unittest discover -s "$s" > /dev/null 2>&1 && echo "PASS: $s" || echo "FAIL: $s"; done
```
Expected: all nine lines print `PASS:` (this mirrors the pwsh loop in `ci.yml`).

6. Commit:
```bash
cd mp_wt && git add .github/workflows/ci.yml AGENTS.md harnesses/kimi-code/replay/README.md && git commit -m "Wire replay suite into CI, document harness (TOOL-039, #110)"
```

---

## Self-Review

### Spec coverage

| Ticket requirement | Where satisfied |
|---|---|
| Replay harness design: recorded signatures vs simulated clock | Task 2 driver (decision-at-each-event, no wall clock); Global Constraints (decision-level, no subprocess fakes) |
| Corpus location | `harnesses/kimi-code/replay/corpus/` — committed, regenerable (durability doctrine); Task 4 |
| CI wiring | Task 6 step 2 (`ci.yml` `$suites`) + step 5 full-matrix local verification |
| Case (1) 2026-09-01 x2 | fixtures `...completion-stall-kill-a/-b`; predicate rule: completion suppression (Task 3) |
| Case (2) 2026-09-12 46 s | fixture `...killed-46s-after-success` |
| Case (3) 2026-09-16/17 degraded | fixtures `...monitor-smb-self-hang`, `...impossible-write-ages-negative-cpu`; abstain rules + `measurement_degraded` expectation |
| Case (4) 2026-08-25 x3 MaxMinutes | fixtures `...maxminutes-slow-vm-kill-1/-2/-3`; `backstop_crossed` expectation pins #108 |
| Case (5) CONTROL 2026-09-25 06/07 | fixtures `...com-wedge-attempt06/-07`; `kill_window_s` + `evidence_keys: [stack_frames]` pin "kill WITH stack evidence" |
| Gates #105–#109 validation | Task 5 production suite + assumed interfaces AI-1..AI-5 stated in Spec |
| Greenfield; fakes exist as substrate | Confirmed: no existing replay harness (inventory §5); fakes deliberately not reused — design note in Global Constraints |

### Placeholder scan

No `TBD`, `TODO`, `FIXME`, `...` bodies, or "appropriate handling" anywhere in the task code. The only forward reference is `monitor:decide_kill` (AI-2) — it is a deliberate skip-gate (`skipUnless`), not an unimplemented stub: at merge the suite is green with `54 tests, 1 skipped`, and the skip flips to a run the day #106 lands.

### Type consistency across tasks

| Field | Producer | Consumers | Type contract |
|---|---|---|---|
| `window.now_s` | driver (Task 2) | predicate (Task 3), bridge target | int/float seconds since dispatch_open |
| `window.dispatch` | case file → driver | predicate | `{agent: str, timeout_s: num, max_minutes?: num}` (schema Task 1) |
| `window.events` | driver | predicate | list of schema-validated event dicts, prefix-growing |
| verdict `.kill` / `.reason` / `.measurement_degraded` / `.evidence` | predicate, production predicate | driver `check_expectations` | bool / str / bool / dict — asserted in Task 2 tests |
| `expect.kill_window_s` | case file | driver | `[lo, hi]` ordered pair, schema-enforced |
| probe sample fields | case file | predicate `_sample_consistent` | numbers, magnitudes UNCHECKED by schema (deliberate, tested in `test_impossible_samples_are_legal_fixture_content`) |

### Review-focus pinning

1. Sampling bias → Task 2 `test_never_sleeps_and_calls_per_event` + AI-2 purity requirement; reviewer checkpoint at #106.
2. Oracle overfitting → Task 3 tests use synthetic-only windows; reviewer greps `reference_predicate.py` for case ids.
3. Fixture magnitude errors → reviewer cross-checks `source_note` durations vs `t` values in Task 4 (46 s at t=1546-1500; 2–3 min kill margins; 25-min silences at 1500 s; 45-min backstop = 2700 s).
4. Frame-format drift → Task 3 `test_confirmed_wedge_kills_with_stack_evidence` asserts exact frames; bridge is the single adaptation point.
5. Bridge import posture → Task 5 `test_missing_module_returns_none` + docstring; only `ImportError` skips.

### Inventory corrections found while surveying (inventory vs worktree @ 8982e24)

1. **ci.yml line range.** Inventory §5/#110 says the `$suites` array is at `ci.yml:22-41`; the array literal is lines **25-34** (22-41 spans the whole "Suite matrix" step). Insertion point for the new suite: after line 29 (`"harnesses/kimi-code/runner/tests",`).
2. **Status vocabulary citation.** Inventory's #107 note cites `delegate.py:49-53` for the envelope status vocabulary; those lines are the EXIT-CODE constants (`EXIT_OK=0, EXIT_INVALID=64, EXIT_INTERNAL=70, EXIT_TIMEOUT=124, EXIT_INTERRUPTED=130`). The status strings (`completed|failed|timeout|interrupted|internal_error`) are set at `_make_result` call sites, not defined at :49-53. Not on this ticket's critical path; noted for the #107 planner.
3. **`docs/` did not exist** at mp_wt root at survey time (a sibling planner created `docs/superpowers/plans/` for TOOL-032 during this session); this plan's directory was created fresh. No conflicts.
4. **Verified accurate** (spot-checked, no correction): `runner.py` 1652 lines with journal fns at :500-598, `run_delegate` at :708-774 (the `cmd` list starts :723, `Popen` at :725-726, kills at :738-740/:754-757), `cmd_status` at :1489-1576, `_make_result` at `delegate.py:1403-1427`, `fake_worker.py` 59 lines, `fake_delegate.py` timeout-mode at :81, hand-written crash signatures at `test_dispatch_journal.py:123-150`, generator idiom `pilot.py:261-263`, sys.path-insert import idiom in codex/core tests, windows-latest-only CI.
5. **New environment fact** the corpus test must absorb: workstation `core.autocrlf=true` with no `.gitattributes` — handled by LF-byte writes in `gen_corpus.py` and CRLF normalization in `test_corpus_regeneration_is_byte_stable` (Task 4).
