#!/usr/bin/env python3
"""Payload-emitted progress heartbeat protocol (#105 / TOOL-034).

Single stdlib-only module carrying BOTH sides of the contract:

- emit(): the reference emitter a payload (or task-instructed worker script)
  calls. The path arrives via DELEGATE_HEARTBEAT_PATH, injected by the
  delegate into the child environment; DELEGATE_DISPATCH_ID correlates the
  record with the runner's journal. Emitting is ALWAYS best-effort: any
  failure returns None. A payload that never emits is a legitimate state
  ("absent"), not an error.
- read_latest()/is_stale(): the bounded, never-raising reader the runner's
  monitor paths use (#109: measurement failure = abstain + report).

Cadence contract: emitters SHOULD emit at least once per NOMINAL_CADENCE_S
while actively working and ALWAYS on stage transitions. Readers treat
silence beyond STALE_AFTER_S as "stale" (reported) — never as death; the
kill decision belongs to the stall guard's conjunctive predicate
(TOOL-035, stall_guard.py), which consumes this protocol and re-judges
staleness by file mtime against its own policy.

Rotation: append-only until one append would exceed MAX_HEARTBEAT_BYTES,
then the file is rewritten keeping the newest ~half from a line boundary
(atomic via os.replace). Single-emitter assumption: concurrent emitters may
interleave lines (each line is self-contained, so reads stay valid) but MUST
NOT both rotate. Torn final lines are the expected post-crash shape and are
skipped by the reader.
"""

import json
import os
import time

SCHEMA_VERSION = 1
MAX_HEARTBEAT_BYTES = 65536
READ_TAIL_BYTES = 16384
NOMINAL_CADENCE_S = 15.0
STALE_AFTER_S = 60.0

REQUIRED_KEYS = ("v", "ts", "epoch", "pid", "seq", "stage")

_SEQ = [0]  # per-process sequence; restarts are visible via pid/epoch


def heartbeat_path_from_env(env=None):
    env = os.environ if env is None else env
    return env.get("DELEGATE_HEARTBEAT_PATH") or None


def emit(stage, sub_stage=None, counters=None, path=None, env=None):
    """Append one heartbeat record. Returns the record, or None on any
    missing path / OSError. Never raises."""
    env = os.environ if env is None else env
    if path is None:
        path = heartbeat_path_from_env(env)
    if not path:
        return None
    _SEQ[0] += 1
    now = time.time()
    rec = {
        "v": SCHEMA_VERSION,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "epoch": now,
        "pid": os.getpid(),
        "seq": _SEQ[0],
        "dispatch_id": env.get("DELEGATE_DISPATCH_ID") or None,
        "stage": str(stage),
        "sub_stage": None if sub_stage is None else str(sub_stage),
        "counters": dict(counters or {}),
    }
    line = json.dumps(rec, sort_keys=True) + "\n"
    try:
        _append_bounded(path, line)
    except OSError:
        return None
    return rec


def _append_bounded(path, line):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if size + len(line.encode("utf-8")) > MAX_HEARTBEAT_BYTES:
        _rotate(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def _rotate(path):
    """Keep the newest ~half, starting at a line boundary. Atomic replace."""
    tail = b""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - MAX_HEARTBEAT_BYTES // 2))
            tail = f.read()
    except OSError:
        pass
    nl = tail.find(b"\n")
    if 0 <= nl < len(tail) - 1:
        tail = tail[nl + 1:]
    tmp = path + ".rotate-tmp"
    with open(tmp, "wb") as f:
        f.write(tail)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_latest(path):
    """Bounded tail read. Returns (record_or_None, status) with status in
    {"ok", "absent", "empty", "corrupt", "unreadable"}. Never raises on
    content problems; a mid-line fragment at the tail window's start fails
    JSON parsing or the REQUIRED_KEYS check and is dropped like any other
    bad line."""
    try:
        size = os.path.getsize(path)
    except FileNotFoundError:
        return None, "absent"
    except OSError:
        return None, "unreadable"
    if size == 0:
        return None, "empty"
    try:
        with open(path, "rb") as f:
            f.seek(max(0, size - READ_TAIL_BYTES))
            data = f.read(READ_TAIL_BYTES)
    except OSError:
        return None, "unreadable"
    latest = None
    bad = 0
    for ln in data.decode("utf-8", "replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(rec, dict) and all(k in rec for k in REQUIRED_KEYS):
            latest = rec
        else:
            bad += 1
    if latest is None:
        return None, "corrupt" if bad else "empty"
    return latest, "ok"


def is_stale(record, now=None, stale_after_s=STALE_AFTER_S):
    """True/False when freshness is judgeable; None when the record carries
    no numeric epoch — callers must report that as degraded measurement,
    never as staleness."""
    if record is None:
        return None
    epoch = record.get("epoch")
    if not isinstance(epoch, (int, float)) or isinstance(epoch, bool):
        return None
    now = time.time() if now is None else now
    return (now - epoch) > stale_after_s
