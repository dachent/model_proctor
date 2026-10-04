# Condition-Based Kill Predicate (TOOL-035) Implementation Plan

> Executors: use the `subagent-driven-development` skill — one subagent per
> task, tasks executed in order (1 → 6); each task is self-contained TDD.

**Planning status resolution:** GitHub issue #106 carries "Planning REQUIRED"
(predicate design, stack-diff mechanics, replay strategy). This document IS
that planning: it fixes the conjunctive predicate's inputs and staleness
windows, the stack-capture comparison mechanics, the escalation ladder, the
timeout-demotion rule, and the replay approach. Executing this plan closes
the ticket's planning phase; the ticket's acceptance phase remains gated on
the TOOL-039 replay port described in Task 6.

## Goal

Replace the delegate's pure wall-clock kill trigger with a conjunctive,
evidence-first predicate: kill only when **(heartbeat stale) AND (progress
counters flat) AND (two consecutive stack captures show the identical frame
list)**. Escalation ladder: capture stack → wait one interval → capture
again → only then kill. Wall-clock timeouts survive as documented
last-resort backstops, never as primary detectors, and every ladder-driven
kill attaches its evidence to the run record (delegate envelope → runner
dispatch journal).

## Architecture

A new stdlib-only module `harnesses/kimi-code/delegate/stall_guard.py` owns
the policy type, the heartbeat/stack-capture file readers (delegate-side
consumers of TOOL-034's emitter protocol), the signature comparators, and a
`run_escalation` ladder with fully injected clocks. The delegate's wait loop
calls the ladder when the wall-clock deadline expires instead of killing
outright; the ladder either convicts (conjunctive kill), extends (live
payload), or abstains to the documented backstop (unobservable payload).
The runner stays wall-clock (its `timeout_s + 120` wrapper is re-documented
as the backstop's backstop — consolidation is #107's scope) and only learns
to journal the new `kill_evidence` envelope key.

## Tech Stack

Python 3.10, standard library only (`dataclasses`, `hashlib`, `json`,
`math`, `os`, `time`) — control-plane constraint, `AGENTS.md:115-116`.
Tests: stdlib `unittest`, discovered per suite
(`python -m unittest discover -s harnesses/kimi-code/delegate/tests -v`);
CI runs `windows-latest` only (`.github/workflows/ci.yml:22-41`). New test
files land in already-discovered suite dirs, so `ci.yml` does not change.

## Spec

- Inventory: `mp_inventory_103-110.md` (session root), §2a/§3a/§4 and the
  `#106 / TOOL-035` plan-feeding note. All file:line references below were
  re-verified against the worktree @ `8982e24`; corrections are collected in
  the final Self-Review section.
- Ticket: GitHub `dachent/model_proctor` issue **#106**, "[TOOL-035]
  Condition-based kill predicate: evidence-first escalation replaces
  silence/wall-clock heuristics". False-kill record and acceptance replays
  are quoted from the issue body (fetched 2026-09-26).
- Interface dependency: **TOOL-034 (#105)** payload heartbeat/progress
  protocol. TOOL-034 is not merged; this plan defines the expected
  interface explicitly (Task 2, "TOOL-034 contract") and lands the
  delegate-side plumbing + readers against it, tested with fake emitters.
  If TOOL-034 ships a different record shape, the adaptation surface is
  exactly two functions (`read_heartbeat`, `request_stack_capture`).

## Global Constraints

1. **Envelope vocabulary is frozen.** Status stays
   `completed|failed|timeout|interrupted|internal_error`
   (`delegate.py:49-53` exit codes; consumed by `runner.py:765`,
   `cascade.py:121` — frozen artifact — and codex adapter). New information
   rides the `error` string (`condition_met_stall` | `wall_clock_backstop`)
   and one new additive envelope key `kill_evidence` (dict | null). Exit
   code 124 for all timeout-class kills is unchanged.
2. **Nesting invariant.** The ladder's worst-case added time must keep the
   delegate ceiling (`timeout + grace + ~30s`, `delegate.py:17-18`) inside
   the runner's wrapper deadline (`timeout_s + 120`, `runner.py:721`).
   Enforced at config validation: `worst_case_added_s() <= 60`
   (`stall_guard.MAX_ADDED_SECONDS`). TOOL-037 owns the full sizing rule.
3. **Trust class.** Heartbeat and stack-capture content is written by the
   payload (or its emitter) into the run_dir; it is worker-influenceable
   and therefore advisory under the project's non-adversarial threat model
   — same class as the dispatch journal (`runner.py:513-515`). The
   predicate must never treat missing evidence as proof of a stall
   (abstain → backstop), matching the #109 "measurement failure = abstain"
   doctrine.
4. **Staleness uses delegate-side clocks only.** Heartbeat staleness is
   judged by `heartbeat.jsonl` file mtime vs. `time.time()`, never by the
   payload-claimed `epoch` field (payload clocks are untrusted).
5. **No git mutations without explicit user confirmation**
   (`AGENTS.md:120`). The commit commands below are run by the implementer
   after confirmation. The implementer also commits this plan file:
   `git add docs/superpowers/plans/2026-09-25-tool-035-kill-predicate.md`
   (fold into Task 1's commit).
6. **Default behavior is byte-identical to today.** Without a
   `stall_guard` block in `agents.json` the legacy wall-clock path runs
   untouched (`error` stays null on timeout; `kill_evidence` is null).
7. Deterministic tests: no wall-clock sleeps in unit tests (injected
   clocks, codex pattern `harnesses/codex/delegate/tests/test_delegate.py:215-243`);
   the four lifecycle integration tests use real short-lived subprocesses
   per the existing `TestLifecycle` idiom (`test_delegate.py:1058-1131`)
   with hard wall-clock bounds.

## Review Focus

Five failure classes the spec implies but no single task's tests fully
exercise — each is pinned to the task whose tests come closest:

1. **Worker-forged liveness** (trust class, Global Constraint 3). A payload
   that keeps writing advancing heartbeats while doing no work defeats
   conjuncts 1–2; stack frames are also payload-emitted. No test can fix
   this; mitigation is that evidence (capture paths, signatures, policy
   snapshot) is journaled for post-hoc audit against wire logs. Pinned to
   Task 3 (`test_evidence_is_complete_for_audit`) and Task 5 (journal
   propagation test).
2. **Observer-side measurement failure** (the 2026-09-16/17 false kills:
   the monitor's own SMB stat hung). A capture timeout or unreadable
   heartbeat file must abstain — never convict — and get exactly one retry
   from the extension budget before the documented backstop. Pinned to
   Task 2 (`test_capture_timeout_returns_not_ok`, `test_missing_heartbeat_file`)
   and Task 3 (`test_measurement_incomplete_abstains_then_backstops`).
3. **Heartbeat flood / unbounded file growth.** A 1 kHz emitter must not
   make each wait-loop observation O(file): reads are tail-bounded to 64
   KiB. Pinned to Task 2 (`test_heartbeat_read_is_tail_bounded`).
4. **Payload clock chaos.** Future/past `epoch` fields and future file
   mtimes must not flip staleness either way: mtime-based staleness,
   negative ages clamped to 0. Pinned to Task 2
   (`test_future_mtime_clamps_to_fresh`) and Task 3
   (`test_payload_epoch_field_is_ignored`).
5. **Completion race during the ladder** (the 2026-09-01 / 2026-09-12
   class: killed after the run had succeeded). `proc_alive` is re-polled
   between every ladder step; a child that exits mid-ladder yields
   `completed_race`, never a kill. Pinned to Task 3
   (`test_completion_race_never_kills`), Task 4
   (`test_child_exiting_mid_ladder_reports_completed`), and Task 6
   (replays 1–4).

---

## Task 1 — `stall_guard.py`: policy type, validation, signature helpers

**Files**
- Create: `harnesses/kimi-code/delegate/stall_guard.py` (partial: header,
  constants, `StallPolicy`, `progress_signature`, `stack_signature`)
- Test: `harnesses/kimi-code/delegate/tests/test_stall_guard.py` (new)

**Interfaces** (consumed by Tasks 2–4 and by `delegate.py`)

```python
class PolicyError(ValueError): ...

@dataclass(frozen=True)
class StallPolicy:
    enabled: bool = False
    heartbeat_stale_after_s: float = 20.0
    capture_interval_s: float = 10.0
    capture_timeout_s: float = 5.0
    max_extensions: int = 1

    @property
    def extension_s(self) -> float: ...            # == heartbeat_stale_after_s
    def worst_case_added_s(self) -> float: ...
    @classmethod
    def from_config(cls, block: dict | None) -> "StallPolicy": ...

MAX_ADDED_SECONDS: float = 60.0
def progress_signature(progress) -> str   # sha256[:16] of canonical JSON
def stack_signature(frames: list) -> str  # sha256[:16] of newline-joined frames
```

Semantics of `from_config`: `None` → disabled default policy (legacy path).
Strict known-keys validation (mirrors `core/task_schema.py` doctrine);
booleans rejected where numbers are expected (`isinstance(v, bool)` guard,
same pattern as `delegate.py:404`); all three time knobs must be finite and
`>= 1.0`; `max_extensions` a non-negative int. `worst_case_added_s()` is
checked `<= MAX_ADDED_SECONDS` only when `enabled` is true.

Worst-case formula (single shared extension budget — every non-convicting
ladder pass consumes one extension):

```
per_eval = 2 * capture_timeout_s + capture_interval_s
worst    = per_eval + max_extensions * (per_eval + max(extension_s, capture_interval_s))
```

Defaults: `20 + 1 * (20 + 20) = 60.0` — exactly at the cap (see Global
Constraint 2).

**Steps**

1. Write the failing test file
   `harnesses/kimi-code/delegate/tests/test_stall_guard.py`:

```python
#!/usr/bin/env python3
"""TOOL-035 (#106): stall-guard policy, readers, and ladder tests."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_DELEGATE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DELEGATE_DIR))

import stall_guard  # noqa: E402


class PolicyTest(unittest.TestCase):

    def test_none_block_yields_disabled_defaults(self):
        p = stall_guard.StallPolicy.from_config(None)
        self.assertFalse(p.enabled)
        self.assertEqual(p.heartbeat_stale_after_s, 20.0)
        self.assertEqual(p.capture_interval_s, 10.0)
        self.assertEqual(p.capture_timeout_s, 5.0)
        self.assertEqual(p.max_extensions, 1)

    def test_defaults_sit_exactly_at_worst_case_cap(self):
        p = stall_guard.StallPolicy.from_config({"enabled": True})
        self.assertEqual(p.worst_case_added_s(), 60.0)
        self.assertLessEqual(p.worst_case_added_s(),
                             stall_guard.MAX_ADDED_SECONDS)

    def test_worst_case_overflow_rejected_when_enabled(self):
        with self.assertRaises(stall_guard.PolicyError):
            stall_guard.StallPolicy.from_config(
                {"enabled": True, "max_extensions": 3})

    def test_worst_case_overflow_allowed_when_disabled(self):
        p = stall_guard.StallPolicy.from_config(
            {"enabled": False, "max_extensions": 99})
        self.assertFalse(p.enabled)

    def test_unknown_keys_rejected(self):
        with self.assertRaises(stall_guard.PolicyError):
            stall_guard.StallPolicy.from_config({"enabled": True, "bogus": 1})

    def test_bool_rejected_where_number_expected(self):
        for key in ("heartbeat_stale_after_s", "capture_interval_s",
                    "capture_timeout_s"):
            with self.subTest(key=key):
                with self.assertRaises(stall_guard.PolicyError):
                    stall_guard.StallPolicy.from_config({key: True})

    def test_non_finite_and_subsecond_knobs_rejected(self):
        for bad in (float("inf"), float("nan"), 0.5, 0, -3):
            with self.subTest(bad=bad):
                with self.assertRaises(stall_guard.PolicyError):
                    stall_guard.StallPolicy.from_config(
                        {"capture_interval_s": bad})

    def test_enabled_must_be_bool(self):
        with self.assertRaises(stall_guard.PolicyError):
            stall_guard.StallPolicy.from_config({"enabled": 1})

    def test_max_extensions_must_be_non_negative_int(self):
        for bad in (-1, 1.5, True, "2"):
            with self.subTest(bad=bad):
                with self.assertRaises(stall_guard.PolicyError):
                    stall_guard.StallPolicy.from_config(
                        {"max_extensions": bad})


class SignatureTest(unittest.TestCase):

    def test_progress_signature_is_order_independent_and_stable(self):
        a = stall_guard.progress_signature({"phase": "run", "done": 3})
        b = stall_guard.progress_signature({"done": 3, "phase": "run"})
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)

    def test_progress_signature_changes_with_counters(self):
        a = stall_guard.progress_signature({"done": 3})
        b = stall_guard.progress_signature({"done": 4})
        self.assertNotEqual(a, b)

    def test_stack_signature_order_sensitive(self):
        a = stall_guard.stack_signature(["a.py:1:f", "b.py:2:g"])
        b = stall_guard.stack_signature(["b.py:2:g", "a.py:1:f"])
        self.assertNotEqual(a, b)

    def test_stack_signature_identical_frames_match(self):
        frames = ["compile.py:10:run", "cli.py:3:<module>"]
        self.assertEqual(stall_guard.stack_signature(frames),
                         stall_guard.stack_signature(list(frames)))


if __name__ == "__main__":
    unittest.main()
```

2. Run it, expect fail (module does not exist):

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/delegate/tests -v -k test_stall_guard
```

(`-k` requires Python 3.11+; on 3.10 run the file directly:
`python harnesses/kimi-code/delegate/tests/test_stall_guard.py -v`.
Expect `ModuleNotFoundError: No module named 'stall_guard'`.)

3. Create `harnesses/kimi-code/delegate/stall_guard.py`:

```python
#!/usr/bin/env python3
"""stall_guard — TOOL-035 (#106) condition-based kill predicate.

Replaces wall-clock/silence kill heuristics with a conjunctive predicate:
kill only when (heartbeat stale) AND (progress counters flat) AND (two
consecutive stack captures show the identical frame list). Escalation
ladder: capture stack -> wait one interval -> capture again -> only then
kill. Wall-clock timeouts survive as documented last-resort backstops,
never primary detectors; every ladder-driven kill attaches its evidence to
the delegate envelope (`kill_evidence`), which the runner journals with the
dispatch record.

Heartbeat / stack-capture file protocol (TOOL-034 interface contract; the
payload-side emitter is TOOL-034's deliverable, this module is the
delegate-side consumer):
  <run_dir>/heartbeat.jsonl        JSON-lines, each
                                   {"beat": int, "epoch": float,
                                    "progress": object}; torn tail
                                   tolerated; staleness judged by FILE MTIME
                                   (delegate-side clock), never `epoch`.
  <run_dir>/stack_request.json     {"seq": int, "requested_at": epoch},
                                   written atomically (tmp + os.replace).
  <run_dir>/stack_capture_<n>.json {"seq": n, "captured_at": epoch,
                                    "frames": [str, ...]}.

If TOOL-034 lands with a different record shape, the adaptation surface is
exactly read_heartbeat() / request_stack_capture().

Trust class (mirrors the journal's, runner.py:513-515): all file content is
worker-influenceable; evidence is advisory under the non-adversarial threat
model, and missing evidence abstains rather than convicting (#109).
"""

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass

HEARTBEAT_FILE = "heartbeat.jsonl"
STACK_REQUEST_FILE = "stack_request.json"
STACK_CAPTURE_FMT = "stack_capture_{seq}.json"

REQUIRED_CAPTURES = 2  # ticket-pinned: two consecutive identical captures

_MAX_HEARTBEAT_READ_BYTES = 65536   # tail-bounded: heartbeat-flood guard
_MAX_CAPTURE_BYTES = 262144

# Worst-case seconds the ladder may add past the wall-clock deadline. Keeps
# the delegate ceiling (timeout + this + kill grace + ~30s, delegate.py:17-18)
# inside the runner's wrapper deadline (timeout_s + 120, runner.py:721).
# TOOL-037 owns the full sizing rule.
MAX_ADDED_SECONDS = 60.0


class PolicyError(ValueError):
    """Invalid stall_guard config block; delegate wraps it as ConfigError."""


@dataclass(frozen=True)
class StallPolicy:
    enabled: bool = False
    heartbeat_stale_after_s: float = 20.0
    capture_interval_s: float = 10.0
    capture_timeout_s: float = 5.0
    max_extensions: int = 1

    @property
    def extension_s(self):
        # One staleness window: a payload that resumed heartbeating during
        # the wait reads fresh at the next pass.
        return self.heartbeat_stale_after_s

    def worst_case_added_s(self):
        per_eval = 2 * self.capture_timeout_s + self.capture_interval_s
        return (per_eval + self.max_extensions *
                (per_eval + max(self.extension_s, self.capture_interval_s)))

    @classmethod
    def from_config(cls, block):
        if block is None:
            return cls()
        if not isinstance(block, dict):
            raise PolicyError("stall_guard must be an object")
        known = {"enabled", "heartbeat_stale_after_s", "capture_interval_s",
                 "capture_timeout_s", "max_extensions"}
        unknown = sorted(set(block) - known)
        if unknown:
            raise PolicyError(f"stall_guard: unknown keys: {unknown}")
        enabled = block.get("enabled", False)
        if not isinstance(enabled, bool):
            raise PolicyError("stall_guard.enabled must be a boolean")
        kwargs = {"enabled": enabled}
        for name in ("heartbeat_stale_after_s", "capture_interval_s",
                     "capture_timeout_s"):
            v = block.get(name, getattr(cls(), name))
            if not isinstance(v, (int, float)) or isinstance(v, bool) \
                    or not math.isfinite(v):
                raise PolicyError(
                    f"stall_guard.{name} must be a finite number")
            if v < 1.0:
                raise PolicyError(f"stall_guard.{name} must be >= 1")
            kwargs[name] = float(v)
        me = block.get("max_extensions", 1)
        if not isinstance(me, int) or isinstance(me, bool) or me < 0:
            raise PolicyError(
                "stall_guard.max_extensions must be a non-negative integer")
        kwargs["max_extensions"] = me
        policy = cls(**kwargs)
        if policy.enabled and \
                policy.worst_case_added_s() > MAX_ADDED_SECONDS:
            raise PolicyError(
                f"stall_guard worst case {policy.worst_case_added_s():.1f}s "
                f"exceeds {MAX_ADDED_SECONDS:.0f}s (must stay inside the "
                "runner's timeout_s + 120 wrapper deadline)")
        return policy


def progress_signature(progress):
    canon = json.dumps(progress, sort_keys=True, separators=(",", ":"),
                       default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def stack_signature(frames):
    canon = "\n".join(str(f) for f in frames)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]
```

4. Run the test file, expect pass:
   `python harnesses/kimi-code/delegate/tests/test_stall_guard.py -v`
   (12 tests pass).

5. Commit (after user confirmation, per Global Constraint 5):

```
cd mp_wt && git add harnesses/kimi-code/delegate/stall_guard.py harnesses/kimi-code/delegate/tests/test_stall_guard.py docs/superpowers/plans/2026-09-25-tool-035-kill-predicate.md && git commit -m "feat(kimi-code): stall-guard policy, validation, and signature helpers (#106)"
```

---

## Task 2 — Heartbeat and stack-capture readers (TOOL-034 contract seam)

**Files**
- Modify: `harnesses/kimi-code/delegate/stall_guard.py` (append readers)
- Test: `harnesses/kimi-code/delegate/tests/test_stall_guard.py` (append
  `ReaderTest`)

**Interfaces** (consumed by Task 3's ladder and Task 4's fixtures)

```python
@dataclass(frozen=True)
class HeartbeatReading:
    seen: bool                  # one full record parsed
    mtime_age_s: float | None   # delegate-side staleness; None when unseen
    progress: object | None     # newest record's "progress" payload
    torn_tail: bool             # trailing partial line (writer mid-append)

@dataclass(frozen=True)
class CaptureReading:
    ok: bool
    seq: int
    signature: str | None
    path: str | None
    frames_count: int

def read_heartbeat(run_dir, now=None) -> HeartbeatReading
def write_stack_request(run_dir, seq, now=None) -> None
def read_stack_capture(run_dir, seq) -> CaptureReading | None
def request_stack_capture(run_dir, seq, timeout_s,
                          sleep=time.sleep, monotonic=time.monotonic,
                          now=None) -> CaptureReading
```

TOOL-034 contract (expected interface, defined here explicitly because
TOOL-034 is unmerged): the delegate injects env var `MP_HEARTBEAT_DIR`
(absolute run_dir path) into the child environment; the payload-side
emitter appends heartbeat records to `$MP_HEARTBEAT_DIR/heartbeat.jsonl`
and answers `$MP_HEARTBEAT_DIR/stack_request.json` by writing
`stack_capture_<seq>.json` within `capture_timeout_s`. Frame entries are
normalized `"path:lineno:func"` strings (faulthandler output is acceptable;
the comparator requires only deterministic equality for identical stacks).

**Steps**

1. Append the failing tests to `test_stall_guard.py` (before the
   `if __name__` block):

```python
class ReaderTest(unittest.TestCase):

    def setUp(self):
        self.run_dir = tempfile.mkdtemp(prefix="sg-run-")
        self.addCleanup(shutil.rmtree, self.run_dir, ignore_errors=True)

    def _write_heartbeat(self, records, mtime=None):
        path = os.path.join(self.run_dir, stall_guard.HEARTBEAT_FILE)
        with open(path, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_missing_heartbeat_file(self):
        r = stall_guard.read_heartbeat(self.run_dir, now=1000.0)
        self.assertFalse(r.seen)
        self.assertIsNone(r.mtime_age_s)

    def test_newest_record_wins_and_age_from_mtime(self):
        self._write_heartbeat(
            [{"beat": 1, "epoch": 999.0, "progress": {"done": 1}},
             {"beat": 2, "epoch": 1.0, "progress": {"done": 2}}],
            mtime=900.0)
        r = stall_guard.read_heartbeat(self.run_dir, now=1000.0)
        self.assertTrue(r.seen)
        self.assertEqual(r.mtime_age_s, 100.0)
        self.assertEqual(r.progress, {"done": 2})

    def test_payload_epoch_field_is_ignored_for_staleness(self):
        # epoch claims the far future; mtime is stale -> stale.
        self._write_heartbeat(
            [{"beat": 1, "epoch": 9e9, "progress": {}}], mtime=100.0)
        r = stall_guard.read_heartbeat(self.run_dir, now=1000.0)
        self.assertEqual(r.mtime_age_s, 900.0)

    def test_future_mtime_clamps_to_fresh(self):
        self._write_heartbeat([{"beat": 1, "epoch": 0, "progress": {}}],
                              mtime=5000.0)
        r = stall_guard.read_heartbeat(self.run_dir, now=1000.0)
        self.assertEqual(r.mtime_age_s, 0.0)

    def test_torn_tail_tolerated_and_flagged(self):
        path = self._write_heartbeat(
            [{"beat": 1, "epoch": 0, "progress": {"done": 7}}])
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"beat": 2, "epo')  # writer died mid-append
        r = stall_guard.read_heartbeat(self.run_dir)
        self.assertTrue(r.seen)
        self.assertTrue(r.torn_tail)
        self.assertEqual(r.progress, {"done": 7})

    def test_heartbeat_read_is_tail_bounded(self):
        # 300k records (~9 MB): the reader must return the newest record
        # without scanning the whole file.
        path = os.path.join(self.run_dir, stall_guard.HEARTBEAT_FILE)
        with open(path, "w", encoding="utf-8") as f:
            for i in range(300000):
                f.write(json.dumps({"beat": i, "epoch": 0,
                                    "progress": {"done": i}}) + "\n")
        r = stall_guard.read_heartbeat(self.run_dir)
        self.assertTrue(r.seen)
        self.assertEqual(r.progress, {"done": 299999})

    def test_capture_roundtrip(self):
        clock = [100.0]
        def responder(d):
            clock[0] += d
            req = os.path.join(self.run_dir, stall_guard.STACK_REQUEST_FILE)
            if os.path.exists(req):
                with open(req, encoding="utf-8") as f:
                    seq = json.load(f)["seq"]
                with open(os.path.join(
                        self.run_dir,
                        stall_guard.STACK_CAPTURE_FMT.format(seq=seq)),
                        "w", encoding="utf-8") as f:
                    json.dump({"seq": seq, "captured_at": clock[0],
                               "frames": ["a.py:1:f", "b.py:2:g"]}, f)
        cap = stall_guard.request_stack_capture(
            self.run_dir, 1, 5.0, sleep=responder,
            monotonic=lambda: clock[0], now=clock[0])
        self.assertTrue(cap.ok)
        self.assertEqual(cap.seq, 1)
        self.assertEqual(cap.frames_count, 2)
        self.assertEqual(cap.signature,
                         stall_guard.stack_signature(["a.py:1:f", "b.py:2:g"]))
        # request file was written atomically: no tmp left behind
        self.assertFalse(os.path.exists(
            os.path.join(self.run_dir, ".stack_request_1.tmp")))

    def test_capture_timeout_returns_not_ok(self):
        clock = [0.0]
        cap = stall_guard.request_stack_capture(
            self.run_dir, 3, 1.0,
            sleep=lambda d: clock.__setitem__(0, clock[0] + d),
            monotonic=lambda: clock[0])
        self.assertFalse(cap.ok)
        self.assertIsNone(cap.signature)
        self.assertGreaterEqual(clock[0], 1.0)

    def test_capture_seq_mismatch_rejected(self):
        with open(os.path.join(self.run_dir, "stack_capture_9.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"seq": 8, "frames": ["x"]}, f)  # stale/wrong seq
        self.assertIsNone(stall_guard.read_stack_capture(self.run_dir, 9))

    def test_oversized_capture_rejected(self):
        with open(os.path.join(self.run_dir, "stack_capture_1.json"),
                  "w", encoding="utf-8") as f:
            f.write(" " * (stall_guard._MAX_CAPTURE_BYTES + 1))
        self.assertIsNone(stall_guard.read_stack_capture(self.run_dir, 1))
```

2. Run, expect fail (`AttributeError: module 'stall_guard' has no attribute
   'read_heartbeat'`).

3. Append to `stall_guard.py`:

```python
@dataclass(frozen=True)
class HeartbeatReading:
    seen: bool
    mtime_age_s: float | None
    progress: object
    torn_tail: bool


def read_heartbeat(run_dir, now=None):
    """Newest heartbeat record; staleness from file mtime, never `epoch`.

    Tail-bounded (_MAX_HEARTBEAT_READ_BYTES): a flooding emitter must not
    make observation O(file). A torn trailing line (writer mid-append) is
    flagged, not fatal — the journal's doctrine, runner.py:542-556. A
    seek-split first line or any mid-file corrupt line is skipped.
    """
    now = time.time() if now is None else now
    path = os.path.join(run_dir, HEARTBEAT_FILE)
    try:
        st = os.stat(path)
    except OSError:
        return HeartbeatReading(False, None, None, False)
    age = max(0.0, now - st.st_mtime)  # future mtimes read as fresh
    try:
        with open(path, "rb") as f:
            if st.st_size > _MAX_HEARTBEAT_READ_BYTES:
                f.seek(-_MAX_HEARTBEAT_READ_BYTES, os.SEEK_END)
            data = f.read()
    except OSError:
        return HeartbeatReading(False, None, None, False)
    text = data.decode("utf-8", errors="replace")
    nonempty = [ln for ln in (l.strip() for l in text.split("\n")) if ln]
    records = []
    torn = False
    for i, ln in enumerate(nonempty):
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            if i == len(nonempty) - 1:
                torn = True
            continue
        if isinstance(rec, dict):
            records.append(rec)
    if not records:
        return HeartbeatReading(False, None, None, torn)
    return HeartbeatReading(True, age, records[-1].get("progress"), torn)


@dataclass(frozen=True)
class CaptureReading:
    ok: bool
    seq: int
    signature: str | None
    path: str | None
    frames_count: int


def write_stack_request(run_dir, seq, now=None):
    """Atomically publish a capture request for the payload-side emitter."""
    now = time.time() if now is None else now
    tmp = os.path.join(run_dir, f".stack_request_{seq}.tmp")
    dst = os.path.join(run_dir, STACK_REQUEST_FILE)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"seq": seq, "requested_at": now}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dst)


def read_stack_capture(run_dir, seq):
    """Parse the emitter's answer; wrong-seq/oversized/corrupt -> None."""
    path = os.path.join(run_dir, STACK_CAPTURE_FMT.format(seq=seq))
    try:
        st = os.stat(path)
        if st.st_size > _MAX_CAPTURE_BYTES:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            rec = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(rec, dict) or rec.get("seq") != seq:
        return None
    frames = rec.get("frames")
    if not isinstance(frames, list):
        return None
    return CaptureReading(True, seq, stack_signature(frames), path,
                          len(frames))


def request_stack_capture(run_dir, seq, timeout_s, sleep=time.sleep,
                          monotonic=time.monotonic, now=None):
    """Request a capture and poll for the answer up to timeout_s.

    A timeout is an observation FAILURE (ok=False) — callers abstain, they
    never convict on it (#109 doctrine).
    """
    try:
        write_stack_request(run_dir, seq, now=now)
    except OSError:
        return CaptureReading(False, seq, None, None, 0)
    deadline = monotonic() + timeout_s
    while True:
        reading = read_stack_capture(run_dir, seq)
        if reading is not None:
            return reading
        remaining = deadline - monotonic()
        if remaining <= 0:
            return CaptureReading(
                False, seq, None,
                os.path.join(run_dir, STACK_CAPTURE_FMT.format(seq=seq)), 0)
        sleep(min(0.1, remaining))
```

4. Run, expect pass:
   `python harnesses/kimi-code/delegate/tests/test_stall_guard.py -v`
   (22 tests pass).

5. Commit:

```
cd mp_wt && git add harnesses/kimi-code/delegate/stall_guard.py harnesses/kimi-code/delegate/tests/test_stall_guard.py && git commit -m "feat(kimi-code): heartbeat and stack-capture readers for the stall guard (#106)"
```

---

## Task 3 — The escalation ladder: `run_escalation`

**Files**
- Modify: `harnesses/kimi-code/delegate/stall_guard.py` (append ladder)
- Test: `harnesses/kimi-code/delegate/tests/test_stall_guard.py` (append
  `FakeWorld` + `LadderTest`)

**Interfaces** (consumed by Task 4's delegate integration)

```python
@dataclass(frozen=True)
class KillVerdict:
    action: str              # "kill" | "completed_race" | "interrupted"
    kill_reason: str | None  # "condition_met_stall" | "wall_clock_backstop"
    evidence: dict           # schema_version 1; see shape below

def run_escalation(run_dir, policy: StallPolicy, proc_alive,
                   interrupted=None, sleep=time.sleep,
                   monotonic=time.monotonic, wall=time.time) -> KillVerdict
```

Ladder semantics (one pass = read heartbeat → capture stack → wait
`capture_interval_s` → capture again → read heartbeat):

- All three conjuncts `True` → `KillVerdict("kill", "condition_met_stall", …)`.
- Any conjunct measured `False` → payload demonstrably live: consume one
  extension, wait `policy.extension_s`, re-run the pass.
- No conjunct `False` but not all `True` → measurement incomplete
  (`abstain = "unobservable_payload"` when all three are `None`, else
  `"measurement_incomplete"`): consume one extension, wait
  `capture_interval_s` (a measurement retry, not a grace period), re-run.
- Extensions exhausted → `KillVerdict("kill", "wall_clock_backstop", …)`
  with the measured conjuncts and abstain reason visible in evidence.
- `proc_alive()` or `interrupted()` is re-polled between every step;
  a child that exits mid-ladder yields `"completed_race"` (never a kill).

Evidence shape (attached to the delegate envelope as `kill_evidence`):

```python
{
    "schema_version": 1,
    "kill_reason": "condition_met_stall" | "wall_clock_backstop" | None,
    "predicate": {"heartbeat_stale": True | False | None,
                  "progress_flat": True | False | None,
                  "stacks_identical": True | False | None,
                  "abstain": None | "unobservable_payload" | "measurement_incomplete"},
    "heartbeat_age_s": float | None,
    "progress_signatures": [str, ...],      # newest-first is NOT guaranteed; audit uses set-equality
    "stack_captures": [{"seq": int, "ok": bool, "signature": str | None,
                        "path": str | None, "frames_count": int}, ...],
    "extensions_used": int,
    "policy": {"heartbeat_stale_after_s": float, "capture_interval_s": float,
               "capture_timeout_s": float, "max_extensions": int},
    "evaluated_at": "YYYY-MM-DDTHH:MM:SS",
}
```

`None` conjunct = unmeasured (abstain doctrine), `False` = measured live,
`True` = measured stall evidence. Progress flatness compares the newest
heartbeat's progress signature at pass start vs. pass end.

**Steps**

1. Append the failing tests to `test_stall_guard.py`:

```python
class FakeWorld:
    """Scripted clock + scripted payload for run_escalation tests."""

    def __init__(self, run_dir):
        self.t = 1000.0
        self.run_dir = run_dir
        self.alive = True
        self.interrupted = False
        self.capture_frames = None   # set to a list to auto-answer captures

    def monotonic(self):
        return self.t

    def wall(self):
        return self.t

    def sleep(self, d):
        self.t += d
        self._maybe_answer_capture()

    def proc_alive(self):
        return self.alive

    def is_interrupted(self):
        return self.interrupted

    def _maybe_answer_capture(self):
        frames = self.capture_frames
        if frames is None:
            return
        req = os.path.join(self.run_dir, stall_guard.STACK_REQUEST_FILE)
        if not os.path.exists(req):
            return
        try:
            with open(req, encoding="utf-8") as f:
                seq = json.load(f)["seq"]
        except (OSError, json.JSONDecodeError):
            return
        if callable(frames):
            frames = frames(seq)
        with open(os.path.join(self.run_dir,
                               stall_guard.STACK_CAPTURE_FMT.format(seq=seq)),
                  "w", encoding="utf-8") as f:
            json.dump({"seq": seq, "captured_at": self.t,
                       "frames": frames}, f)

    def write_heartbeat(self, progress, age_s):
        path = os.path.join(self.run_dir, stall_guard.HEARTBEAT_FILE)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"beat": 1, "epoch": self.t - age_s,
                                "progress": progress}) + "\n")
        past = self.t - age_s
        os.utime(path, (past, past))  # mtime drives staleness, not epoch


def _policy(**kw):
    base = {"enabled": True, "heartbeat_stale_after_s": 5.0,
            "capture_interval_s": 2.0, "capture_timeout_s": 3.0,
            "max_extensions": 1}
    base.update(kw)
    return stall_guard.StallPolicy.from_config(base)


def _run(world, policy):
    return stall_guard.run_escalation(
        world.run_dir, policy, proc_alive=world.proc_alive,
        interrupted=world.is_interrupted, sleep=world.sleep,
        monotonic=world.monotonic, wall=world.wall)


class LadderTest(unittest.TestCase):

    def setUp(self):
        self.run_dir = tempfile.mkdtemp(prefix="sg-ladder-")
        self.addCleanup(shutil.rmtree, self.run_dir, ignore_errors=True)

    def test_all_conjuncts_true_kills_with_evidence(self):
        w = FakeWorld(self.run_dir)
        w.capture_frames = ["compile.py:10:run", "cli.py:3:<module>"]
        w.write_heartbeat({"done": 0}, age_s=100.0)  # stale, frozen
        v = _run(w, _policy())
        self.assertEqual(v.action, "kill")
        self.assertEqual(v.kill_reason, "condition_met_stall")
        self.assertEqual(v.evidence["predicate"],
                         {"heartbeat_stale": True, "progress_flat": True,
                          "stacks_identical": True, "abstain": None})
        caps = v.evidence["stack_captures"]
        self.assertEqual(len(caps), 2)
        self.assertTrue(all(c["ok"] for c in caps))
        self.assertEqual(caps[0]["signature"], caps[1]["signature"])
        self.assertEqual(v.evidence["extensions_used"], 0)
        self.assertEqual(v.evidence["kill_reason"], "condition_met_stall")
        self.assertTrue(v.evidence["evaluated_at"])

    def test_fresh_advancing_payload_extends_then_backstops(self):
        w = FakeWorld(self.run_dir)
        w.capture_frames = ["a.py:1:f"]
        w.write_heartbeat({"done": 1}, age_s=0.0)
        real_sleep = w.sleep
        def advancing(d):
            real_sleep(d)
            # progress advances on every wait; heartbeat re-stamped fresh
            w.write_heartbeat({"done": w.t}, age_s=0.0)
        w.sleep = advancing
        v = _run(w, _policy())
        self.assertEqual(v.action, "kill")
        self.assertEqual(v.kill_reason, "wall_clock_backstop")
        self.assertFalse(v.evidence["predicate"]["heartbeat_stale"])
        self.assertFalse(v.evidence["predicate"]["progress_flat"])
        self.assertIsNone(v.evidence["predicate"]["abstain"])
        self.assertEqual(v.evidence["extensions_used"], 1)

    def test_unobservable_payload_backstops_with_abstain(self):
        w = FakeWorld(self.run_dir)  # no heartbeat file, no capture answers
        v = _run(w, _policy())
        self.assertEqual(v.action, "kill")
        self.assertEqual(v.kill_reason, "wall_clock_backstop")
        self.assertEqual(v.evidence["predicate"],
                         {"heartbeat_stale": None, "progress_flat": None,
                          "stacks_identical": None,
                          "abstain": "unobservable_payload"})
        self.assertEqual(v.evidence["extensions_used"], 1)

    def test_max_extensions_zero_backstops_immediately(self):
        w = FakeWorld(self.run_dir)
        v = _run(w, _policy(max_extensions=0))
        self.assertEqual((v.action, v.kill_reason),
                         ("kill", "wall_clock_backstop"))
        self.assertEqual(v.evidence["extensions_used"], 0)

    def test_measurement_incomplete_abstains_then_backstops(self):
        # Stale + flat heartbeats, but the capture channel is dead: the two
        # measured conjuncts say "stalled", the missing third abstains.
        w = FakeWorld(self.run_dir)
        w.write_heartbeat({"done": 0}, age_s=100.0)
        v = _run(w, _policy())
        self.assertEqual((v.action, v.kill_reason),
                         ("kill", "wall_clock_backstop"))
        self.assertEqual(v.evidence["predicate"]["heartbeat_stale"], True)
        self.assertEqual(v.evidence["predicate"]["progress_flat"], True)
        self.assertIsNone(v.evidence["predicate"]["stacks_identical"])
        self.assertEqual(v.evidence["predicate"]["abstain"],
                         "measurement_incomplete")

    def test_differing_stacks_read_live(self):
        w = FakeWorld(self.run_dir)
        w.write_heartbeat({"done": 0}, age_s=100.0)  # stale + flat...
        # ...but every capture shows a different frame: the stack is moving.
        w.capture_frames = lambda seq: ["worker.py:%d:loop" % seq]
        v = _run(w, _policy())
        self.assertEqual(v.kill_reason, "wall_clock_backstop")
        self.assertFalse(v.evidence["predicate"]["stacks_identical"])
        self.assertIsNone(v.evidence["predicate"]["abstain"])

    def test_completion_race_never_kills(self):
        w = FakeWorld(self.run_dir)
        w.write_heartbeat({"done": 0}, age_s=100.0)
        real_sleep = w.sleep
        def die_during_interval(d):
            real_sleep(d)
            w.alive = False  # child exits while the ladder waits
        w.sleep = die_during_interval
        v = _run(w, _policy())
        self.assertEqual(v.action, "completed_race")
        self.assertIsNone(v.kill_reason)
        self.assertIsNone(v.evidence["kill_reason"])

    def test_interrupted_aborts_ladder(self):
        w = FakeWorld(self.run_dir)
        w.interrupted = True
        v = _run(w, _policy())
        self.assertEqual(v.action, "interrupted")
        self.assertIsNone(v.kill_reason)

    def test_evidence_is_complete_for_audit(self):
        w = FakeWorld(self.run_dir)
        w.capture_frames = ["f.py:1:g"]
        w.write_heartbeat({"done": 0}, age_s=50.0)
        v = _run(w, _policy())
        ev = v.evidence
        for key in ("schema_version", "kill_reason", "predicate",
                    "heartbeat_age_s", "progress_signatures",
                    "stack_captures", "extensions_used", "policy",
                    "evaluated_at"):
            self.assertIn(key, ev)
        self.assertEqual(ev["policy"]["capture_timeout_s"], 3.0)
        self.assertTrue(ev["progress_signatures"])
        for cap in ev["stack_captures"]:
            self.assertTrue(cap["path"].endswith(
                f"stack_capture_{cap['seq']}.json"))
```

2. Run, expect fail (`AttributeError: ... 'run_escalation'`).

3. Append to `stall_guard.py`:

```python
@dataclass(frozen=True)
class KillVerdict:
    action: str               # "kill" | "completed_race" | "interrupted"
    kill_reason: str | None   # "condition_met_stall" | "wall_clock_backstop"
    evidence: dict


def _new_evidence(policy):
    return {
        "schema_version": 1,
        "kill_reason": None,
        "predicate": {"heartbeat_stale": None, "progress_flat": None,
                      "stacks_identical": None, "abstain": None},
        "heartbeat_age_s": None,
        "progress_signatures": [],
        "stack_captures": [],
        "extensions_used": 0,
        "policy": {"heartbeat_stale_after_s": policy.heartbeat_stale_after_s,
                   "capture_interval_s": policy.capture_interval_s,
                   "capture_timeout_s": policy.capture_timeout_s,
                   "max_extensions": policy.max_extensions},
        "evaluated_at": None,
    }


def _stamp(evidence, wall):
    evidence["evaluated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S",
                                             time.localtime(wall()))
    return evidence


def _abstain_reason(conjuncts):
    if any(c is False for c in conjuncts):
        return None
    if all(c is None for c in conjuncts):
        return "unobservable_payload"
    return "measurement_incomplete"


def run_escalation(run_dir, policy, proc_alive, interrupted=None,
                   sleep=time.sleep, monotonic=time.monotonic,
                   wall=time.time):
    """Escalation ladder, entered when the wall-clock deadline expires.

    One pass = heartbeat read -> capture stack -> wait capture_interval_s ->
    capture again -> heartbeat read. Kill only when heartbeat stale AND
    progress flat AND both captures show the identical frame list
    (REQUIRED_CAPTURES = 2, ticket-pinned). Every non-convicting pass
    consumes one extension: a measured-False conjunct (live payload) waits
    extension_s; an unmeasured one (abstain doctrine, #109) waits only
    capture_interval_s — a measurement retry, not a grace period. Budget
    exhausted -> documented wall_clock_backstop kill with the measured
    conjuncts on record. proc_alive/interrupted are re-polled between every
    step: a child that exits mid-ladder is completed_race, never a kill.
    """
    interrupted = interrupted or (lambda: False)
    evidence = _new_evidence(policy)
    seq = [0]
    extensions = [0]

    def _wait(seconds):
        end = monotonic() + seconds
        while True:
            if not proc_alive():
                return "completed_race"
            if interrupted():
                return "interrupted"
            remaining = end - monotonic()
            if remaining <= 0:
                return None
            sleep(min(0.1, remaining))

    def _capture():
        seq[0] += 1
        cap = request_stack_capture(run_dir, seq[0], policy.capture_timeout_s,
                                    sleep=sleep, monotonic=monotonic,
                                    now=wall())
        evidence["stack_captures"].append({
            "seq": cap.seq, "ok": cap.ok, "signature": cap.signature,
            "path": cap.path, "frames_count": cap.frames_count})
        return cap

    def _pass():
        hb1 = read_heartbeat(run_dir, now=wall())
        cap1 = _capture()
        abort = _wait(policy.capture_interval_s)
        if abort:
            return abort
        cap2 = _capture()
        hb2 = read_heartbeat(run_dir, now=wall())
        if not proc_alive():
            return "completed_race"
        if interrupted():
            return "interrupted"
        heartbeat_stale = (hb2.mtime_age_s > policy.heartbeat_stale_after_s
                           if hb2.seen else None)
        if hb2.seen:
            evidence["heartbeat_age_s"] = hb2.mtime_age_s
        sig1 = progress_signature(hb1.progress) if hb1.seen else None
        sig2 = progress_signature(hb2.progress) if hb2.seen else None
        evidence["progress_signatures"] = [s for s in (sig1, sig2)
                                           if s is not None]
        progress_flat = (sig1 == sig2) if (sig1 and sig2) else None
        stacks_identical = (cap1.signature == cap2.signature
                            if (cap1.ok and cap2.ok) else None)
        return (heartbeat_stale, progress_flat, stacks_identical)

    while True:
        result = _pass()
        if result in ("completed_race", "interrupted"):
            return KillVerdict(result, None, _stamp(evidence, wall))
        conjuncts = result
        pred = evidence["predicate"]
        (pred["heartbeat_stale"], pred["progress_flat"],
         pred["stacks_identical"]) = conjuncts
        if all(c is True for c in conjuncts):
            evidence["kill_reason"] = "condition_met_stall"
            return KillVerdict("kill", "condition_met_stall",
                               _stamp(evidence, wall))
        if extensions[0] >= policy.max_extensions:
            pred["abstain"] = _abstain_reason(conjuncts)
            evidence["kill_reason"] = "wall_clock_backstop"
            return KillVerdict("kill", "wall_clock_backstop",
                               _stamp(evidence, wall))
        extensions[0] += 1
        evidence["extensions_used"] = extensions[0]
        live = any(c is False for c in conjuncts)
        if not live:
            pred["abstain"] = _abstain_reason(conjuncts)
        abort = _wait(policy.extension_s if live
                      else policy.capture_interval_s)
        if abort:
            return KillVerdict(abort, None, _stamp(evidence, wall))
```

4. Run, expect pass:
   `python harnesses/kimi-code/delegate/tests/test_stall_guard.py -v`
   (31 tests pass).

5. Commit:

```
cd mp_wt && git add harnesses/kimi-code/delegate/stall_guard.py harnesses/kimi-code/delegate/tests/test_stall_guard.py && git commit -m "feat(kimi-code): conjunctive escalation ladder with evidence verdicts (#106)"
```

---

## Task 4 — Delegate integration: config, env plumbing, wait-loop ladder, envelope

**Files**
- Modify: `harnesses/kimi-code/delegate/delegate.py` (7 hunks below)
- Modify: `harnesses/kimi-code/delegate/agents.example.json` (document the block)
- Modify: `harnesses/kimi-code/delegate/README.md` (append the section below)
- Modify: `scripts/install.py` (register the new module)
- Test: `harnesses/kimi-code/delegate/tests/test_delegate.py`
  (`_ENVELOPE_KEYS`, new fixtures, new `TestStallGuardLifecycle`)

**Interfaces**
- Consumes (Task 1–3): `stall_guard.StallPolicy.from_config`,
  `stall_guard.PolicyError`, `stall_guard.run_escalation`,
  `KillVerdict.action/.kill_reason/.evidence`.
- Produces (consumed by Task 5's runner changes): envelope key
  `kill_evidence: dict | None`; timeout-path `error` ∈
  `{None, "condition_met_stall", "wall_clock_backstop"}`.
- Produces (consumed by TOOL-034's emitter): child env var
  `MP_HEARTBEAT_DIR` = absolute run_dir path.

**Steps**

1. Write the failing tests. In `harnesses/kimi-code/delegate/tests/test_delegate.py`:

   a. Add to `_ENVELOPE_KEYS` (`test_delegate.py:315-333`), after
      `"error": (str, type(None)),`:

```python
    "kill_evidence": (dict, type(None)),
```

   b. Add fixture scripts after `_PID_SLEEPER` (`test_delegate.py:193-201`):

```python
# TOOL-035: wedged worker — heartbeats flow briefly then stop (stale),
# progress frozen, but a capture handler keeps answering with the identical
# frozen frame list. The conjunctive predicate must convict this child.
_STALL_GUARD_WEDGED = r'''
import json
import os
import time

hb_dir = os.environ["MP_HEARTBEAT_DIR"]
hb = os.path.join(hb_dir, "heartbeat.jsonl")
for beat in range(3):
    with open(hb, "a", encoding="utf-8") as f:
        f.write(json.dumps({"beat": beat, "epoch": time.time(),
                            "progress": {"phase": "compile",
                                         "items_done": 0}}) + "\n")
    time.sleep(0.2)
req_path = os.path.join(hb_dir, "stack_request.json")
while True:
    if os.path.exists(req_path):
        try:
            with open(req_path, encoding="utf-8") as f:
                seq = json.load(f)["seq"]
            with open(os.path.join(hb_dir, "stack_capture_%d.json" % seq),
                      "w", encoding="utf-8") as f:
                json.dump({"seq": seq, "captured_at": time.time(),
                           "frames": ["compile.py:10:run",
                                      "cli.py:3:<module>"]}, f)
        except Exception:
            pass
    time.sleep(0.2)
'''

# TOOL-035: healthy worker — heartbeats keep flowing with advancing
# progress and changing frames. The predicate must never convict it; only
# the documented capacity backstop may fire, with live conjuncts on record.
_STALL_GUARD_PROGRESSING = r'''
import json
import os
import time

hb_dir = os.environ["MP_HEARTBEAT_DIR"]
hb = os.path.join(hb_dir, "heartbeat.jsonl")
req_path = os.path.join(hb_dir, "stack_request.json")
beat = 0
while True:
    beat += 1
    with open(hb, "a", encoding="utf-8") as f:
        f.write(json.dumps({"beat": beat, "epoch": time.time(),
                            "progress": {"phase": "compile",
                                         "items_done": beat}}) + "\n")
    if os.path.exists(req_path):
        try:
            with open(req_path, encoding="utf-8") as f:
                seq = json.load(f)["seq"]
            with open(os.path.join(hb_dir, "stack_capture_%d.json" % seq),
                      "w", encoding="utf-8") as f:
                json.dump({"seq": seq, "captured_at": time.time(),
                           "frames": ["worker.py:%d:loop" % beat]}, f)
        except Exception:
            pass
    time.sleep(0.2)
'''
```

   c. Append the test class after `TestLifecycle` (file end, before any
      trailing `if __name__` block — verify placement against the actual
      file tail; the class must sit at module level):

```python
class TestStallGuardLifecycle(DelegateTestBase):
    """TOOL-035 (#106): condition-based kill predicate, end to end."""

    _SG = {"enabled": True, "heartbeat_stale_after_s": 1,
           "capture_interval_s": 1, "capture_timeout_s": 2,
           "max_extensions": 1}

    def _sg_config(self, script, **policy_overrides):
        sg = dict(self._SG)
        sg.update(policy_overrides)
        return self._config({
            "test-agent": make_agent(script, prompt_delivery="argument",
                                     default_timeout=3, minimum_timeout=1,
                                     maximum_timeout=300),
        }, extra={"default_kill_grace_seconds": 1, "stall_guard": sg})

    def test_condition_met_stall_kills_with_evidence(self):
        wedged = self._script("sg_wedged", _STALL_GUARD_WEDGED)
        cfg = self._sg_config(wedged)
        t0 = time.monotonic()
        out, err, rc = self._run("test-agent", task="ignored", config=cfg,
                                 timeout_wrap=90)
        wall = time.monotonic() - t0
        result = self._assert_result(out, err, rc, "timeout", 124)
        self.assertEqual(result["error"], "condition_met_stall")
        ev = result["kill_evidence"]
        self.assertEqual(ev["kill_reason"], "condition_met_stall")
        self.assertEqual(ev["predicate"],
                         {"heartbeat_stale": True, "progress_flat": True,
                          "stacks_identical": True, "abstain": None})
        sigs = [c["signature"] for c in ev["stack_captures"]]
        self.assertEqual(len(sigs), 2)
        self.assertEqual(sigs[0], sigs[1])
        self.assertLess(wall, 45)

    def test_progressing_child_dies_only_by_documented_backstop(self):
        progressing = self._script("sg_progressing", _STALL_GUARD_PROGRESSING)
        cfg = self._sg_config(progressing)
        t0 = time.monotonic()
        out, err, rc = self._run("test-agent", task="ignored", config=cfg,
                                 timeout_wrap=90)
        wall = time.monotonic() - t0
        result = self._assert_result(out, err, rc, "timeout", 124)
        self.assertEqual(result["error"], "wall_clock_backstop")
        ev = result["kill_evidence"]
        self.assertEqual(ev["kill_reason"], "wall_clock_backstop")
        self.assertFalse(ev["predicate"]["heartbeat_stale"])
        self.assertFalse(ev["predicate"]["progress_flat"])
        self.assertIsNone(ev["predicate"]["abstain"])
        self.assertEqual(ev["extensions_used"], 1)
        self.assertLess(wall, 45)

    def test_unobservable_legacy_child_backstops_with_abstain(self):
        sleeper = self._script("sg_legacy", _PID_SLEEPER)
        pid_file = os.path.join(self.workspace, "legacy_pid.txt")
        cfg = self._config({
            "test-agent": make_agent(sleeper, prompt_delivery="argument",
                                     extra_args=[pid_file],
                                     default_timeout=3, minimum_timeout=1,
                                     maximum_timeout=300),
        }, extra={"default_kill_grace_seconds": 1,
                  "stall_guard": dict(self._SG)})
        t0 = time.monotonic()
        out, err, rc = self._run("test-agent", task="ignored", config=cfg,
                                 timeout_wrap=90)
        wall = time.monotonic() - t0
        result = self._assert_result(out, err, rc, "timeout", 124)
        self.assertEqual(result["error"], "wall_clock_backstop")
        self.assertEqual(result["kill_evidence"]["predicate"]["abstain"],
                         "unobservable_payload")
        self.assertLess(wall, 45)

    def test_stall_guard_disabled_preserves_legacy_timeout(self):
        sleeper = self._script("sg_off", _PID_SLEEPER)
        pid_file = os.path.join(self.workspace, "off_pid.txt")
        cfg = self._config({
            "test-agent": make_agent(sleeper, prompt_delivery="argument",
                                     extra_args=[pid_file],
                                     default_timeout=3, minimum_timeout=1,
                                     maximum_timeout=300),
        }, extra={"default_kill_grace_seconds": 1})
        t0 = time.monotonic()
        out, err, rc = self._run("test-agent", task="ignored", config=cfg,
                                 timeout_wrap=60)
        wall = time.monotonic() - t0
        result = self._assert_result(out, err, rc, "timeout", 124)
        self.assertIsNone(result["error"])
        self.assertIsNone(result["kill_evidence"])
        # No ladder: bounded by timeout + grace + overhead, as before.
        self.assertLess(wall, 20)

    def test_child_exiting_mid_ladder_reports_completed(self):
        # Heartbeats go stale (predicate would arm), but the child exits
        # during the ladder's first interval wait -> completed, never a kill.
        script = self._script("sg_race", _STALL_GUARD_WEDGED.replace(
            "while True:", "time.sleep(4)\nimport sys; sys.exit(0)\nwhile False:", 1))
        cfg = self._sg_config(script)
        out, err, rc = self._run("test-agent", task="ignored", config=cfg,
                                 timeout_wrap=90)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertIsNone(result["kill_evidence"])
```

2. Run the new class, expect fail (`assertEqual(result["error"],
   "condition_met_stall")` gets `None`; `_ENVELOPE_KEYS` fails on the
   missing `kill_evidence` key):

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
```

3. Implement. Hunks for `harnesses/kimi-code/delegate/delegate.py`:

   **Hunk A** — import, after `from pathlib import Path` (line 33):

```python
import stall_guard  # TOOL-035 (#106): condition-based kill predicate
```

   **Hunk B** — module docstring ceiling note (lines 17–18), replace:

```
Actual wall-clock ceiling ≈ timeout + default_kill_grace_seconds + overhead
(taskkill invocations, proc.wait, reader joins) ≈ timeout + grace + ~30 s.
```

   with:

```
Actual wall-clock ceiling ≈ timeout + default_kill_grace_seconds + overhead
(taskkill invocations, proc.wait, reader joins) ≈ timeout + grace + ~30 s.
With stall_guard enabled (TOOL-035) the deadline is a documented backstop
that triggers the evidence ladder instead of an immediate kill; add up to
stall_guard.MAX_ADDED_SECONDS (60 s) for the ladder. Config validation keeps
the total inside the runner's timeout_s + 120 wrapper deadline.
```

   **Hunk C** — config validation, in `_validate_config` immediately after
   the `max_log_bytes` optional block (`delegate.py:406-411`):

```python
    # TOOL-035 (#106): optional condition-based kill predicate block.
    if "stall_guard" in cfg:
        try:
            stall_guard.StallPolicy.from_config(cfg["stall_guard"])
        except stall_guard.PolicyError as e:
            raise ConfigError(str(e))
```

   **Hunk D** — policy resolution, after the `validate_timeout` block
   (`delegate.py:1123-1127`):

```python
    # TOOL-035: re-parse (cheap, pure) so the wait loop gets a typed policy;
    # from_config(None) yields the disabled legacy policy.
    stall_policy = stall_guard.StallPolicy.from_config(cfg.get("stall_guard"))
```

   **Hunk E** — env injection, immediately after
   `run_dir, acl_warning = create_run_dir()` (`delegate.py:1155`):

```python
    # TOOL-034/035: the payload's heartbeat emitter (TOOL-034) appends to
    # heartbeat.jsonl and answers stack_request.json here; the stall-guard
    # ladder (TOOL-035) reads them. Unconditional and inert for legacy
    # payloads that never look at it.
    child_env["MP_HEARTBEAT_DIR"] = run_dir
```

   **Hunk F** — wait loop (`delegate.py:1249-1264`), replace the
   `timed_out = False` … deadline-expiry block with:

```python
    timed_out = False
    kill_reason = None      # TOOL-035: "condition_met_stall" | "wall_clock_backstop"
    kill_evidence = None    # TOOL-035: predicate evidence for the envelope
    try:
        while True:
            if _interrupted.is_set():
                break
            rc = proc.poll()
            if rc is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Final poll: the child may have exited during the last sleep.
                rc = proc.poll()
                if rc is not None:
                    break
                if stall_policy.enabled:
                    # TOOL-035 (#106): the deadline is a documented backstop,
                    # not a verdict. The ladder owns the clock from here
                    # (bounded by policy.worst_case_added_s(), which config
                    # validation keeps inside the runner's +120 wrapper).
                    verdict = stall_guard.run_escalation(
                        run_dir, stall_policy,
                        proc_alive=lambda: proc.poll() is None,
                        interrupted=_interrupted.is_set)
                    if verdict.action == "kill":
                        timed_out = True
                        kill_reason = verdict.kill_reason
                        kill_evidence = verdict.evidence
                        break
                    if verdict.action == "interrupted":
                        break  # _interrupted is set; the interruption path runs
                    continue  # completed_race: re-poll; the child is exiting
                timed_out = True
                break
            time.sleep(min(0.1, remaining))
    except KeyboardInterrupt:
```

   **Hunk G** — timeout path (`delegate.py:1320-1335`): in the
   `_make_result("timeout", …)` call add two kwargs:

```python
            child_home=child_home,
            error=kill_reason,
            kill_evidence=kill_evidence,
        )
```

   (replacing the current trailing `child_home=child_home,\n        )`.)

   **Hunk H** — `_make_result` (`delegate.py:1403-1427`): add
   `kill_evidence=None` to the signature (after `error=None`) and
   `"kill_evidence": kill_evidence,` after `"error": error,` in the dict.

   **Hunk I** — `harnesses/kimi-code/delegate/agents.example.json`, after
   the `"max_log_bytes": 67108864,` line:

```json
  "stall_guard": {
    "enabled": false,
    "heartbeat_stale_after_s": 20,
    "capture_interval_s": 10,
    "capture_timeout_s": 5,
    "max_extensions": 1
  },
```

   **Hunk J** — `scripts/install.py`:
   - Line 34: `DELEGATE_FILES = ["delegate.py", "stall_guard.py", "catalog.py", "agents.example.json", "README.md"]`
   - Line 45: `REQUIRED_AFTER_INSTALL = ["runner.py", "delegate.py", "stall_guard.py", "catalog.py", "task_schema.py", "pricing.yaml"]`

   **Hunk K** — append to `harnesses/kimi-code/delegate/README.md`:

```markdown
## Stall guard (TOOL-035)

With a top-level `"stall_guard"` block in agents.json (`"enabled": true`),
the wall-clock timeout becomes a documented backstop that triggers an
evidence ladder instead of an immediate kill: capture the payload's stack,
wait one `capture_interval_s`, capture again, and kill only when the
heartbeat is stale AND progress counters are flat AND both captures show
the identical frame list. A payload that measures live on any conjunct is
extended (up to `max_extensions`); an unobservable payload abstains to the
backstop. Kills on this path attach `kill_evidence` to the result envelope
(`error` = `condition_met_stall` or `wall_clock_backstop`). The heartbeat /
stack-capture file protocol in the run directory is TOOL-034's emitter
contract (`MP_HEARTBEAT_DIR`); see `stall_guard.py`'s module docstring.
```

4. Run the full delegate suite, expect pass:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
```

   Also run the flat-install smoke the CI performs (covers Hunk J):

```
cd mp_wt && python -c "import sys; sys.path.insert(0, 'scripts'); import install; missing = [n for n in install.REQUIRED_AFTER_INSTALL if not (install.KIMI / 'delegate' / n).is_file() and not (install.KIMI / 'runner' / n).is_file() and not (install.ROOT / 'core' / n).is_file() and not (install.ROOT / 'evals' / n).is_file()]; print('missing:', missing); sys.exit(1 if missing else 0)"
```

   (Expect `missing: []` — `install.KIMI` points at the repo's
   `harnesses/kimi-code` tree, so `stall_guard.py` is found.)

5. Commit:

```
cd mp_wt && git add harnesses/kimi-code/delegate/delegate.py harnesses/kimi-code/delegate/agents.example.json harnesses/kimi-code/delegate/README.md harnesses/kimi-code/delegate/tests/test_delegate.py scripts/install.py && git commit -m "feat(kimi-code): wire stall-guard ladder into the delegate wait loop (#106)"
```

---

## Task 5 — Runner: kill evidence on the run record, backstop re-documentation

**Files**
- Modify: `harnesses/kimi-code/runner/runner.py` (4 hunks below)
- Modify: `harnesses/kimi-code/runner/tests/fake_worker.py` (one env knob)
- Test: `harnesses/kimi-code/runner/tests/test_dispatch_journal.py` (append
  one test method)

**Interfaces**
- Consumes (Task 4): envelope key `kill_evidence: dict | None`.
- Produces: journal `dispatch_finished` records and `state["dispatches"]`
  entries carry `kill_evidence`; `cmd_status` output gains
  `last_kill_evidence` (dict | null, additive — `cmd_status` merges `state`
  at top level already, `runner.py:1574`, so readers tolerate new keys).

The runner deliberately does NOT get the predicate: it kills the delegate
*wrapper*, not the payload tree, and single-kill-authority consolidation is
#107's scope. This task only re-documents `timeout_s + 120` as the
backstop's backstop and propagates evidence.

**Steps**

1. Write the failing test. Append to
   `harnesses/kimi-code/runner/tests/test_dispatch_journal.py`, inside
   `DispatchJournalTest` (after `test_timeout_leaves_a_finished_pair`,
   `test_dispatch_journal.py:181-191`):

```python
    # ── TOOL-035: kill evidence rides the journal, state, and status ────
    def test_kill_evidence_propagates_to_journal_state_and_status(self):
        ws, task, sdir = self._setup_ready()
        evidence = {"schema_version": 1, "kill_reason": "condition_met_stall",
                    "predicate": {"heartbeat_stale": True,
                                  "progress_flat": True,
                                  "stacks_identical": True,
                                  "abstain": None},
                    "stack_captures": [{"seq": 1, "ok": True,
                                        "signature": "aaa"},
                                       {"seq": 2, "ok": True,
                                        "signature": "aaa"}],
                    "extensions_used": 0}
        env = {"FAKE_WORKER_MODE": "timeout",
               "FAKE_WORKER_KILL_EVIDENCE": json.dumps(evidence)}
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_WORKER), env_extra=env)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["envelope_status"], "timeout")
        finished = [e for e in journal_lines(sdir)
                    if e["event"] == "dispatch_finished"]
        self.assertEqual(finished[-1]["kill_evidence"]["kill_reason"],
                         "condition_met_stall")
        rc, out = run_runner("status", "--workspace", ws)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["last_kill_evidence"]["kill_reason"],
                         "condition_met_stall")

    def test_dispatch_without_kill_evidence_journals_null(self):
        ws, task, sdir = self._setup_ready()
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_WORKER))
        self.assertEqual(rc, 0, out)
        finished = [e for e in journal_lines(sdir)
                    if e["event"] == "dispatch_finished"]
        self.assertIn("kill_evidence", finished[-1])
        self.assertIsNone(finished[-1]["kill_evidence"])
        rc, out = run_runner("status", "--workspace", ws)
        self.assertIsNone(out["last_kill_evidence"])
```

2. Run, expect fail (`KeyError: 'kill_evidence'` on the journal record and
   `FAKE_WORKER_KILL_EVIDENCE` unrecognized):

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```

3. Implement. Hunks for `harnesses/kimi-code/runner/runner.py`:

   **Hunk R1** — `cmd_dispatch` state record (`runner.py:1004-1013`): add
   one line after `"child_home": envelope.get("child_home"),`:

```python
            # TOOL-035: condition-kill evidence travels with the run record.
            "kill_evidence": envelope.get("kill_evidence"),
```

   **Hunk R2** — `dispatch_finished` journal record
   (`runner.py:1024-1030`): add after `"heartbeats": heartbeat_count[0],`:

```python
        "kill_evidence": envelope.get("kill_evidence"),
```

   **Hunk R3** — `cmd_status` output (`runner.py:1552`, after the
   `"last_dispatch"` line):

```python
        # TOOL-035: the last dispatch's kill evidence is visible without
        # opening state.json by hand.
        "last_kill_evidence": (state.get("dispatches") or [{}])[-1].get(
            "kill_evidence"),
```

   **Hunk R4** — `run_delegate` docstring (`runner.py:709-716`), replace
   the closing sentences:

```
    interval instead of one full timeout. The kill semantics are unchanged: past
    timeout + 120s grace the child is killed and a timeout envelope returned
    (the delegate enforces the same ceiling on its side)."""
```

   with:

```
    interval instead of one full timeout. Kill semantics (TOOL-035):
    timeout_s + 120 is a documented last-resort BACKSTOP, never the primary
    stall detector — the delegate's condition-based predicate (stall_guard,
    when enabled) owns the kill decision and attaches kill_evidence to the
    envelope. This wrapper kill exists only for a wedged delegate process;
    consolidating the two kill sites is #107's scope."""
```

   And at `runner.py:721`, above `deadline = time.monotonic() + timeout_s + 120`,
   no code change — the docstring carries the demotion (the deadline value
   is unchanged; TOOL-037 owns re-sizing).

4. Modify `harnesses/kimi-code/runner/tests/fake_worker.py`:

   a. Docstring env-knob list (lines 8-10), append:

```
  FAKE_WORKER_KILL_EVIDENCE  JSON string; attached to the envelope as
                             kill_evidence (TOOL-035 propagation tests)
```

   b. Replace the emit block (lines 36-54):

```python
    mode = os.environ.get("FAKE_WORKER_MODE", "completed")
    status, child_rc, exit_code = {
        "completed": ("completed", 0, 0),
        "failed": ("failed", 1, 0),
        "timeout": ("timeout", None, 124),
    }[mode]

    sys.stdout.write(json.dumps({
        "schema_version": 1,
        "status": status,
        "agent": args.agent,
        "child_exit_code": child_rc,
        "duration_seconds": 0.01,
        "stdout": f"fake worker {args.agent} (mode={mode})",
        "stderr": "",
        "run_dir": None,
        "error": None if status in ("completed", "failed") else f"fake_{status}",
        "child_session_id": "fake-session-0001" if status == "completed" else None,
    }) + "\n")
    return exit_code
```

   with:

```python
    mode = os.environ.get("FAKE_WORKER_MODE", "completed")
    status, child_rc, exit_code = {
        "completed": ("completed", 0, 0),
        "failed": ("failed", 1, 0),
        "timeout": ("timeout", None, 124),
    }[mode]

    envelope = {
        "schema_version": 1,
        "status": status,
        "agent": args.agent,
        "child_exit_code": child_rc,
        "duration_seconds": 0.01,
        "stdout": f"fake worker {args.agent} (mode={mode})",
        "stderr": "",
        "run_dir": None,
        "error": None if status in ("completed", "failed") else f"fake_{status}",
        "child_session_id": "fake-session-0001" if status == "completed" else None,
    }
    evidence_json = os.environ.get("FAKE_WORKER_KILL_EVIDENCE")
    if evidence_json:
        envelope["kill_evidence"] = json.loads(evidence_json)
    sys.stdout.write(json.dumps(envelope) + "\n")
    return exit_code
```

5. Run the runner suite, expect pass:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```

6. Commit:

```
cd mp_wt && git add harnesses/kimi-code/runner/runner.py harnesses/kimi-code/runner/tests/fake_worker.py harnesses/kimi-code/runner/tests/test_dispatch_journal.py && git commit -m "feat(kimi-code): journal stall-guard kill evidence with the run record (#106)"
```

---

## Task 6 — Replay acceptance: the five dated incidents from issue #106

**Files**
- Test: `harnesses/kimi-code/delegate/tests/test_stall_guard_replay.py` (new)

**Interfaces**
- Consumes (Tasks 1–3): `stall_guard.run_escalation`,
  `StallPolicy.from_config`, `KillVerdict`.
- Produces (for TOOL-039 / #110): five named replay scenarios encoded as
  scripted observation worlds; when TOOL-039's journal-level replay harness
  lands, these five ports are the first fixtures. The ticket's acceptance
  checkbox stays open until that port exists — the implementer must state
  this in the issue's "Final evidence and handoff" section rather than
  closing #106 on unit evidence alone.

Scenario mapping (issue #106 acceptance list):

| Incident | Historical event | Replay model | Expected verdict |
|---|---|---|---|
| 2026-09-01 attempt A | watchdog killed run 2–3 min AFTER job #7 succeeded | child already exited when the ladder starts | `completed_race`, no kill |
| 2026-09-01 attempt B | same, second occurrence | same | `completed_race`, no kill |
| 2026-09-12 | killed 46 s after the final workbook succeeded | child exits during the first interval wait | `completed_race`, no kill |
| 2026-09-16/17 | kills on corrupted measurements (monitor's SMB stat hung) | first pass unobservable (no capture answers, no heartbeat file); observations recover healthy in pass 2; child exits during the extension | `completed_race`, no kill; pass-1 abstain on record |
| 2026-09-25 attempts 06/07 | genuine COM wedge (HardQuiet was a correct kill) | heartbeats stop, progress frozen, both captures answer identical frozen frames | `kill`, `condition_met_stall`, two identical capture signatures on record |

**Steps**

1. Write the failing test file
   `harnesses/kimi-code/delegate/tests/test_stall_guard_replay.py`:

```python
#!/usr/bin/env python3
"""TOOL-035 (#106) acceptance replays: the five dated false-/true-kill
incidents from the issue body, replayed as scripted observation worlds
against run_escalation with fully injected clocks (no wall-clock sleeps).

When TOOL-039's journal-level replay harness lands, port these five
scenarios to it as fixtures; until then this file IS the replay gate.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_DELEGATE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DELEGATE_DIR))

import stall_guard  # noqa: E402


class ReplayWorld:
    """Virtual clock + scripted payload for incident replays."""

    def __init__(self, run_dir):
        self.t = 0.0
        self.run_dir = run_dir
        self.alive = True
        self.capture_frames = None   # None = capture channel dead
        self.observations_live = True
        self._beat = 0

    def monotonic(self):
        return self.t

    def wall(self):
        return self.t

    def sleep(self, d):
        self.t += d
        self._maybe_answer_capture()

    def proc_alive(self):
        return self.alive

    # ── scripted payload behaviors ────────────────────────────────────
    def heartbeat(self, items_done):
        """Emit one heartbeat and stamp the file mtime at virtual now."""
        self._beat += 1
        path = os.path.join(self.run_dir, stall_guard.HEARTBEAT_FILE)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"beat": self._beat, "epoch": self.t,
                                "progress": {"items_done": items_done}}) + "\n")
        os.utime(path, (self.t, self.t))

    def advance_with_progress(self, seconds):
        """Live payload: advancing progress, fresh heartbeats."""
        steps = max(1, int(seconds))
        for _ in range(steps):
            self.t += seconds / steps
            self._beat += 1
            self.heartbeat(self._beat)
            self._maybe_answer_capture()

    def _maybe_answer_capture(self):
        frames = self.capture_frames
        if frames is None or not self.observations_live:
            return
        req = os.path.join(self.run_dir, stall_guard.STACK_REQUEST_FILE)
        if not os.path.exists(req):
            return
        try:
            with open(req, encoding="utf-8") as f:
                seq = json.load(f)["seq"]
        except (OSError, json.JSONDecodeError):
            return
        with open(os.path.join(self.run_dir,
                               stall_guard.STACK_CAPTURE_FMT.format(seq=seq)),
                  "w", encoding="utf-8") as f:
            json.dump({"seq": seq, "captured_at": self.t,
                       "frames": frames}, f)


def _policy():
    return stall_guard.StallPolicy.from_config(
        {"enabled": True, "heartbeat_stale_after_s": 20,
         "capture_interval_s": 10, "capture_timeout_s": 5,
         "max_extensions": 1})


def _replay(world):
    return stall_guard.run_escalation(
        world.run_dir, _policy(), proc_alive=world.proc_alive,
        sleep=world.sleep, monotonic=world.monotonic, wall=world.wall)


class IncidentReplayTest(unittest.TestCase):

    def setUp(self):
        self.run_dir = tempfile.mkdtemp(prefix="sg-replay-")
        self.addCleanup(shutil.rmtree, self.run_dir, ignore_errors=True)

    def _assert_no_kill(self, verdict):
        self.assertEqual(verdict.action, "completed_race")
        self.assertIsNone(verdict.kill_reason)
        self.assertIsNone(verdict.evidence["kill_reason"])

    # 2026-09-01 (x2): killed minutes AFTER the job had succeeded.
    def test_replay_2026_09_01_attempts_a_and_b(self):
        for attempt in ("A", "B"):
            with self.subTest(attempt=attempt):
                w = ReplayWorld(self.run_dir)
                w.alive = False  # the payload had already succeeded
                v = _replay(w)
                # The ladder burns at most one capture timeout discovering
                # the exit, then reports the race — never a kill.
                self._assert_no_kill(v)

    # 2026-09-12: killed 46 s after the final workbook succeeded. The work
    # was done; under the delegate the exiting process must read as
    # completed, not as a stall. Modelled deterministically: the child exits
    # during the ladder's first interval wait (any exit point between ladder
    # entry and conviction is the same class; the wait-loop poll covers
    # exits before entry).
    def test_replay_2026_09_12(self):
        w = ReplayWorld(self.run_dir)
        w.capture_frames = ["excel.py:42:save"]
        w.heartbeat(items_done=7)
        real_sleep = w.sleep
        def exit_during_first_interval(d):
            real_sleep(d)
            if w.t >= 3.0:
                w.alive = False
        w.sleep = exit_during_first_interval
        v = _replay(w)
        self._assert_no_kill(v)

    # 2026-09-16/17: kills on corrupted measurements (observer-side).
    def test_replay_2026_09_16_17(self):
        w = ReplayWorld(self.run_dir)
        # Pass 1: observation channel dead (the monitor's own stat hung).
        w.observations_live = False
        w.capture_frames = ["etl.py:10:run"]
        recovered = {"done": False}
        real_sleep = w.sleep
        def recover_and_finish(d):
            real_sleep(d)
            if not recovered["done"] and w.t >= 25.0:
                # Measurement recovers; the payload is healthy and working.
                recovered["done"] = True
                w.observations_live = True
                w.advance_with_progress(5)
            if w.t >= 40.0:
                # The run completes during the second pass's interval wait —
                # before any conviction, so the verdict must be the race.
                w.alive = False
        w.sleep = recover_and_finish
        v = _replay(w)
        self._assert_no_kill(v)
        # The corrupted first pass is on record as an abstain, not a verdict.
        self.assertIn(v.evidence["predicate"]["abstain"],
                      ("unobservable_payload", "measurement_incomplete", None))

    # 2026-09-25 attempts 06/07: genuine COM wedge — the correct kill.
    def test_replay_2026_09_25_genuine_wedge_kills_with_stack_evidence(self):
        w = ReplayWorld(self.run_dir)
        # Heartbeats flowed, then the event loop wedged: file goes stale,
        # progress frozen; the capture handler answers with the identical
        # frozen frame list.
        w.capture_frames = ["excel.py:88:com_call", "worker.py:12:run"]
        w.heartbeat(items_done=6)
        w.t += 120.0  # stale: no heartbeat for two minutes
        stale_path = os.path.join(self.run_dir, stall_guard.HEARTBEAT_FILE)
        os.utime(stale_path, (w.t - 120.0, w.t - 120.0))
        v = _replay(w)
        self.assertEqual(v.action, "kill")
        self.assertEqual(v.kill_reason, "condition_met_stall")
        pred = v.evidence["predicate"]
        self.assertEqual(pred, {"heartbeat_stale": True,
                                "progress_flat": True,
                                "stacks_identical": True, "abstain": None})
        caps = v.evidence["stack_captures"]
        self.assertEqual(len(caps), 2)
        self.assertEqual(caps[0]["signature"], caps[1]["signature"])
        self.assertTrue(all(c["ok"] for c in caps))


if __name__ == "__main__":
    unittest.main()
```

   Note on `test_replay_2026_09_12`: the payload's heartbeats go stale at
   the deadline (last beat at t=0), captures answer with a *constant* frame
   list — but the child exits during the first interval wait (virtual t
   crosses 46 inside `_wait`), so the verdict must be `completed_race`
   before any conjunct evaluation completes. That is precisely the
   "killed after success" class: observation in flight, payload already
   done.

2. Run, expect fail (`test_replay_2026_09_25_genuine_wedge_kills_with_stack_evidence`
   passes only once Tasks 1–3 are in place; on a partial tree the module
   import or ladder semantics fail):

```
cd mp_wt && python harnesses/kimi-code/delegate/tests/test_stall_guard_replay.py -v
```

3. No new implementation is expected — this task is the acceptance gate
   for Tasks 1–3. If a replay fails, fix the ladder/readers in
   `stall_guard.py`, not the replay expectations, unless the replay model
   itself is shown to diverge from the incident record (document the
   divergence in the commit message and the issue).

4. Run the full delegate + runner suites, expect pass:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/delegate/tests -v && python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```

5. Commit:

```
cd mp_wt && git add harnesses/kimi-code/delegate/tests/test_stall_guard_replay.py && git commit -m "test(kimi-code): replay the five #106 kill incidents against the predicate (#106)"
```

6. Close-out: on issue #106, record that the predicate, ladder, evidence
   propagation, and the five replays pass at the delegate seam; leave the
   acceptance checkbox for the TOOL-039 journal-level port open and
   reference this plan file's path. Do not mark #106 fully closed until
   TOOL-039 consumes the five scenarios.

---

## Self-review

### Spec coverage

| Issue #106 requirement | Where satisfied |
|---|---|
| Conjunctive predicate (heartbeat stale AND progress flat AND two identical stack captures) | `run_escalation._pass` + conviction branch (Task 3); `test_all_conjuncts_true_kills_with_evidence` |
| Escalation ladder (capture → wait one interval → capture → only then kill) | one `_pass` is exactly capture→wait→capture; conviction requires a full pass (Task 3) |
| Timeouts demoted to documented backstops | delegate docstring Hunk B + wait-loop Hunk F (Task 4); runner docstring Hunk R4 (Task 5); `wall_clock_backstop` reason distinguishes backstop kills from verdicts |
| Kill evidence attached to the run record | envelope `kill_evidence` (Task 4) → `state["dispatches"]` + `dispatch_finished` journal record + `status.last_kill_evidence` (Task 5) |
| Consume TOOL-034's heartbeat protocol as an explicit interface dependency | contract defined in Task 2 (file layout, env var, record shapes, adaptation seam); `MP_HEARTBEAT_DIR` injection in Task 4 Hunk E |
| Replays: 2026-09-01 ×2, 2026-09-12, 2026-09-16/17 → NO kill; 2026-09-25 → kill with stack evidence | Task 6, five subtests/tests |
| Codex stalled-IO test idiom as pattern reference | injected-clock fakes (`FakeWorld`, `ReplayWorld`), no wall-clock sleeps in unit tests (Tasks 2, 3, 6) |

### Placeholder scan

No "TBD", no "add appropriate error handling", no deferred design. Every
task's steps contain complete code: module, tests, config example,
installer registration, README section, and exact commit commands. The one
deliberate open item — the TOOL-039 port of the five replays — is an
explicit cross-ticket dependency with a named handoff, not a placeholder.

### Type consistency across tasks

- `StallPolicy` fields are floats except `enabled: bool` and
  `max_extensions: int`; `from_config` coerces int → float for time knobs
  and rejects bools-as-numbers. Tasks 3–6 construct policies only via
  `from_config`.
- `KillVerdict.action` ∈ `{"kill", "completed_race", "interrupted"}`;
  `kill_reason` ∈ `{"condition_met_stall", "wall_clock_backstop", None}` —
  the same strings appear in delegate Hunk F/G, the envelope `error` field,
  fake_worker, and all assertions.
- Evidence dict shape is fixed in Task 3 and asserted verbatim in Tasks 4
  (`result["kill_evidence"]["predicate"]`), 5 (journal record), and 6.
- Python 3.10: PEP 604 unions in annotations only; no 3.11+ syntax.
  (`unittest -k` is 3.11+, so run commands invoke test files directly.)

### Review-focus pinning

| Review Focus class | Owning task(s) and test(s) |
|---|---|
| 1. Worker-forged liveness | Task 3 `test_evidence_is_complete_for_audit`; Task 5 propagation tests (evidence reaches the journal for audit) |
| 2. Observer-side measurement failure | Task 2 `test_capture_timeout_returns_not_ok`, `test_missing_heartbeat_file`; Task 3 `test_measurement_incomplete_abstains_then_backstops`, `test_unobservable_payload_backstops_with_abstain`; Task 6 replay 2026-09-16/17 |
| 3. Heartbeat flood | Task 2 `test_heartbeat_read_is_tail_bounded` (300k-record file) |
| 4. Payload clock chaos | Task 2 `test_payload_epoch_field_is_ignored_for_staleness`, `test_future_mtime_clamps_to_fresh` |
| 5. Completion race during ladder | Task 3 `test_completion_race_never_kills`; Task 4 `test_child_exiting_mid_ladder_reports_completed`; Task 6 replays 2026-09-01/09-12 |

### Inventory corrections found while surveying (mp_inventory_103-110.md)

1. **Latent bug, not in the inventory:** `runner.py:763-774` — the
   `empty delegate output` guard is duplicated (`if not line:` twice,
   :763-766 and :767-769); the second block is dead, and both it (:769)
   and the `json.JSONDecodeError` handler (:773-774) reference
   `r.stderr`/`r.stdout` — `r` is undefined in `run_delegate`, so an
   unparseable delegate envelope raises `NameError` instead of returning
   the intended `internal_error` envelope. Out of scope for #106 (none of
   this plan's hunks touch those lines); recommend filing a separate issue.
2. **Addition to §5/§6:** the inventory does not flag that a new
   delegate-side module requires installer registration —
   `scripts/install.py:34` (`DELEGATE_FILES`) and `:45`
   (`REQUIRED_AFTER_INSTALL`, covered by the CI flat-install smoke,
   `ci.yml:42-47`). Task 4 Hunk J owns this.
3. **Addition to §5:** adding an envelope key requires extending
   `_ENVELOPE_KEYS` in `harnesses/kimi-code/delegate/tests/test_delegate.py:315-333`
   (schema assertion helper). Task 4 step 1a owns this.
4. Verified exact as inventoried (no correction): `kill_process_tree`
   :865-926 and its two callers :1271-1273/:1306-1308; wait loop
   :1249-1265; deadline anchor :1187-1188; `is_pid_alive` :221-227
   test-only (grep: production-site count is zero);
   `cmd_status` :1489-1576 with `stall_after` :1508 and `stall_suspected`
   :1559; `run_delegate` :708-774 with wrapper deadline :721 and kills
   :738-740/:754-757; `_journal_append` :504-532; heartbeat write :989-996;
   `dispatch_finished` :1024-1030; codex pattern tests :215-243;
   `_make_result` :1403-1427; `DelegateTestBase` :349; `fake_worker.py`
   env-knob idiom.
