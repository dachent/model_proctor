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
