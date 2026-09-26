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

Heartbeat / stack-capture file protocol. The heartbeat half is TOOL-034's
shipped contract (#105, delegate/heartbeat.py); the stack-capture half is
defined here (TOOL-034 shipped no stack responder — this module is the
delegate-side consumer and the payload-side responder is a future emitter
extension):
  heartbeat file        JSON-lines at DELEGATE_HEARTBEAT_PATH (injected by
                        the delegate; runner-forwarded via --heartbeat-file,
                        defaulted to <run_dir>/heartbeat.jsonl for standalone
                        stall-guard runs). TOOL-034 record shape:
                        {"v", "ts", "epoch", "pid", "seq", "dispatch_id",
                         "stage", "sub_stage", "counters"}; the predicate's
                        progress payload is `counters`. Torn tail tolerated;
                        staleness judged by FILE MTIME (delegate-side clock),
                        never the payload-claimed `epoch`.
  <hb_dir>/stack_request.json     {"seq": int, "requested_at": epoch},
                                  written atomically (tmp + os.replace).
  <hb_dir>/stack_capture_<n>.json {"seq": n, "captured_at": epoch,
                                   "frames": [str, ...]}.
  <hb_dir> = dirname(DELEGATE_HEARTBEAT_PATH); the payload derives the
  observation directory from the path it already receives — no second
  env var.

Trust class (mirrors the journal's): all file content is
worker-influenceable; evidence is advisory under the non-adversarial threat
model, and missing evidence abstains rather than convicting (#109).
"""

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass

HEARTBEAT_FILE = "heartbeat.jsonl"  # default name for standalone runs
STACK_REQUEST_FILE = "stack_request.json"
STACK_CAPTURE_FMT = "stack_capture_{seq}.json"

REQUIRED_CAPTURES = 2  # ticket-pinned: two consecutive identical captures

_MAX_HEARTBEAT_READ_BYTES = 65536   # tail-bounded: heartbeat-flood guard
_MAX_CAPTURE_BYTES = 262144

# Worst-case seconds the ladder may add past the wall-clock deadline. Keeps
# the delegate ceiling (timeout + this + kill grace + ~30s) inside the
# runner's wrapper deadline (timeout_s + 120). TOOL-037 owns the full
# sizing rule.
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


@dataclass(frozen=True)
class HeartbeatReading:
    seen: bool
    mtime_age_s: float | None
    progress: object
    torn_tail: bool


def read_heartbeat(heartbeat_path, now=None):
    """Newest heartbeat record; staleness from file mtime, never `epoch`.

    `heartbeat_path` is the full TOOL-034 heartbeat file path
    (DELEGATE_HEARTBEAT_PATH), not a directory. Tail-bounded
    (_MAX_HEARTBEAT_READ_BYTES): a flooding emitter must not make
    observation O(file). A torn trailing line (writer mid-append) is
    flagged, not fatal — the journal's doctrine. A seek-split first line
    or any mid-file corrupt line is skipped. The returned `progress` is the
    newest record's `counters` payload (TOOL-034 record shape).
    """
    now = time.time() if now is None else now
    try:
        st = os.stat(heartbeat_path)
    except OSError:
        return HeartbeatReading(False, None, None, False)
    age = max(0.0, now - st.st_mtime)  # future mtimes read as fresh
    try:
        with open(heartbeat_path, "rb") as f:
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
    return HeartbeatReading(True, age, records[-1].get("counters"), torn)


@dataclass(frozen=True)
class CaptureReading:
    ok: bool
    seq: int
    signature: str | None
    path: str | None
    frames_count: int


def write_stack_request(obs_dir, seq, now=None):
    """Atomically publish a capture request for the payload-side emitter."""
    now = time.time() if now is None else now
    tmp = os.path.join(obs_dir, f".stack_request_{seq}.tmp")
    dst = os.path.join(obs_dir, STACK_REQUEST_FILE)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"seq": seq, "requested_at": now}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dst)


def read_stack_capture(obs_dir, seq):
    """Parse the emitter's answer; wrong-seq/oversized/corrupt -> None."""
    path = os.path.join(obs_dir, STACK_CAPTURE_FMT.format(seq=seq))
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


def request_stack_capture(obs_dir, seq, timeout_s, sleep=time.sleep,
                          monotonic=time.monotonic, now=None):
    """Request a capture and poll for the answer up to timeout_s.

    A timeout is an observation FAILURE (ok=False) — callers abstain, they
    never convict on it (#109 doctrine).
    """
    try:
        write_stack_request(obs_dir, seq, now=now)
    except OSError:
        return CaptureReading(False, seq, None, None, 0)
    deadline = monotonic() + timeout_s
    while True:
        reading = read_stack_capture(obs_dir, seq)
        if reading is not None:
            return reading
        remaining = deadline - monotonic()
        if remaining <= 0:
            return CaptureReading(
                False, seq, None,
                os.path.join(obs_dir, STACK_CAPTURE_FMT.format(seq=seq)), 0)
        sleep(min(0.1, remaining))


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


def run_escalation(heartbeat_path, policy, proc_alive, interrupted=None,
                   sleep=time.sleep, monotonic=time.monotonic,
                   wall=time.time):
    """Escalation ladder, entered when the wall-clock deadline expires.

    `heartbeat_path` is the TOOL-034 heartbeat file (DELEGATE_HEARTBEAT_PATH);
    stack-capture request/response files live beside it. One pass =
    heartbeat read -> capture stack -> wait capture_interval_s -> capture
    again -> heartbeat read. Kill only when heartbeat stale AND progress
    flat AND both captures show the identical frame list
    (REQUIRED_CAPTURES = 2, ticket-pinned). Every non-convicting pass
    consumes one extension: a measured-False conjunct (live payload) waits
    extension_s; an unmeasured one (abstain doctrine, #109) waits only
    capture_interval_s — a measurement retry, not a grace period. Budget
    exhausted -> documented wall_clock_backstop kill with the measured
    conjuncts on record. proc_alive/interrupted are re-polled between every
    step: a child that exits mid-ladder is completed_race, never a kill.
    """
    interrupted = interrupted or (lambda: False)
    obs_dir = os.path.dirname(heartbeat_path) or "."
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
        cap = request_stack_capture(obs_dir, seq[0], policy.capture_timeout_s,
                                    sleep=sleep, monotonic=monotonic,
                                    now=wall())
        evidence["stack_captures"].append({
            "seq": cap.seq, "ok": cap.ok, "signature": cap.signature,
            "path": cap.path, "frames_count": cap.frames_count})
        return cap

    def _pass():
        hb1 = read_heartbeat(heartbeat_path, now=wall())
        cap1 = _capture()
        abort = _wait(policy.capture_interval_s)
        if abort:
            return abort
        cap2 = _capture()
        hb2 = read_heartbeat(heartbeat_path, now=wall())
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
