# Payload Heartbeats + Observer Isolation (mp#105 + mp#109) Implementation Plan

**Planning status resolution:** this plan IS the planning that mp#105 (TOOL-034) and mp#109 (TOOL-038) required. Executing it to completion closes the planning phase of both tickets; per-repo closure doctrine (`AGENTS.md:131-136`) still requires a non-empty `## Final evidence and handoff` section on each GitHub issue before either is closed.

> **Executor note:** execute this plan with the `subagent-driven-development` skill — one subagent per task, in task order; tasks 1→6 are sequentially dependent (each builds on the previous task's interfaces).

## Goal

Give the control plane two capabilities it lacks today:

1. **mp#105 / TOOL-034** — a local, append-only, payload-emitted progress heartbeat protocol (schema, cadence, rotation), an emission API payloads can call, and a watcher-side consumption contract in the runner, so a live-but-silent worker is distinguishable from a progressing one by the worker's own testimony rather than leader-side inference.
2. **mp#109 / TOOL-038** — observer isolation: monitors read ONLY local runner-state and payload-emitted files; every workspace measurement carries a wall-clock budget; the failure doctrine is unified — **measurement failure on a monitor path = abstain + `measurement_degraded` report, never kill, never refuse; measurement failure on an acceptance path = fail closed (the refusal IS the abstention)**.

## Architecture

The payload appends JSON heartbeat lines to a runner-owned file (`<state>/heartbeats/<dispatch_id>.jsonl`, outside the workspace, on local disk); the delegate passes the path and dispatch id into the child environment exactly like it injects `KIMI_CODE_HOME` and the nesting marker, and the runner's existing 10s dispatch poll drains the file into `dispatch_progress` journal records. Observer isolation is enforced by wrapping every workspace-tree measurement (`tree_signature` / `config_surface`) in a daemon-thread wall-clock budget (`_measure_with_deadline`) at the acceptance call sites, and by a codified doctrine block in `runner.py` separating monitor paths (abstain + degrade) from acceptance paths (fail closed).

## Tech Stack

Python 3.10, **standard library only** (control-plane constraint, `AGENTS.md:115-116`). Tests: stdlib `unittest`, one discovery dir per suite; CI is `windows-latest` only (`.github/workflows/ci.yml`). Worktree: `mp_wt/` = `dachent/model_proctor` @ `8982e24` (branch `tool-032-detached-custody`). All paths below are relative to `mp_wt/`.

## Spec

- Inventory doc (read first): `mp_inventory_103-110.md` at the session root — sections **#105 / TOOL-034** and **#109 / TOOL-038** plan-feeding notes, plus §4 (monitoring/liveness) and §3b (runner budgets).
- GitHub issues: `dachent/model_proctor` **#105** (TOOL-034, payload heartbeat/progress protocol) and **#109** (TOOL-038, observer isolation, "measurement failure = abstain").
- **Inventory corrections found while surveying (plan wins where they disagree):**
  1. The inventory's #109 note says a wedged tree "blocks status/verify/accept indefinitely". Surveyed: `cmd_status` (`runner.py:1489-1576`) never calls `tree_signature`/`config_surface` — its only filesystem reads are the journal, receipt, and state file, all in the local state dir. The exposed commands are `init` (`tree_signature` at `runner.py:841`), `verify` (`:1064,1145,1167,1194`, plus `config_surface` at `:1069`), and `accept` (`:1287`). This plan's regression test (`test_status_never_measures_the_tree`, Task 5) pins status's isolation so it stays that way.
  2. Inventory cites `_porcelain_entries:353-356`; that is the `subprocess.run` call. The function spans `runner.py:345-376`; the fail-closed raise is `:357-363`.
  3. `_sha256_file` (`runner.py:148-149`) is a single `Path.read_bytes()` — no chunked read exists, so a per-chunk deadline inside the hasher is impossible. A blocking read syscall cannot be preempted by pre-checks at all; this is why Task 5 isolates the *whole measurement* in a daemon thread rather than threading deadlines through `_walk_files`.
  4. The delegate test helper `run_delegate` (`test_delegate.py:279-301`) accepts no extra CLI args, and `fake_worker.py`'s argparse would reject the new `--heartbeat-file`/`--dispatch-id` flags — both are extended as part of this plan (Tasks 2 and 3).
  5. Platform check run while planning (Windows, CPython 3.14 — behavior identical on 3.10): a path whose intermediate component is a FILE raises `FileNotFoundError` from `os.path.getsize`/`os.makedirs` (ERROR_PATH_NOT_FOUND), not `NotADirectoryError`. So that failure shape reads as heartbeat-`absent`, not `unreadable`; the Task 1 `unreadable` test instead forces the branch with a patched `getsize` raising `PermissionError`. The `emit`-failure test's shape (parent path *is* the existing file → `FileExistsError` from `makedirs`) was verified live and works as written.

## Global Constraints

- Stdlib-only Python 3.10 for everything below (`AGENTS.md:115-116`); no new dependencies.
- **Heartbeat data is NEVER a kill input.** Kill predicates stay wall-clock-only; condition-based killing is mp#106 / TOOL-035 and is out of scope. A stale, absent, or corrupt heartbeat may only ever produce a report.
- The runner's existing 10s runner-generated `dispatch_heartbeat` journal cadence (`runner.py:743-752,989-996`) stays as the liveness floor; payload progress is additive, and absence of payload data must never regress existing journal/status behavior.
- The journal (`runner.py:504-532`) remains append-only, fsync'd per record, torn-tail tolerant on read (`runner.py:535-557`). `dispatch_progress` records join it; no existing event shape changes (only additive fields on `dispatch_finished`).
- Envelope vocabulary (`completed|failed|timeout|interrupted|internal_error`, `delegate.py:49-53`) is untouched; no envelope schema change in this plan (`assert_envelope_schema` in `test_delegate.py:336` must stay green unmodified).
- `harnesses/kimi-code/cascade/` is a frozen research artifact — do not touch it. Cascade never passes the new delegate flags, which is fine: no flags = no env vars = no emission = `absent`, by design.
- Any custom `--delegate` shim that rejects unknown CLI args breaks when the runner forwards the new flags; `fake_worker.py` is fixed in Task 3, and this is called out in the handoff notes for the issue.
- Commits: mp repo convention (see `git log`) is free-form/conventional subjects with issue refs — use the exact messages given per task. **Repo rule `AGENTS.md:120`: no git mutations without explicit user confirmation — the executor surfaces each commit command for confirmation before running it.** `git add` with explicit paths only.
- Windows-native: CI is `windows-latest`; path handling must use `pathlib`/`os.path`, and any timing-sensitive test must not rely on sub-second sleeps (CI machines are slow).

## Review Focus

Five input classes / failure modes the spec implies but no single task's happy-path test would catch on its own. Each is pinned to an owning test below; reviewers should check these first.

1. **Torn / truncated final line** — the payload (or runner) dies mid-append, leaving a partial JSON line. Readers must skip it and return the last complete record, never raise. → Task 1 `test_torn_tail_is_skipped`; Task 4 `test_status_degrades_on_corrupt_heartbeat`.
2. **Rotation race and oversized file** — the emitter rotates (truncate-to-tail) while a reader has the file open; the retained tail can begin mid-line. Reads are bounded to the last `READ_TAIL_BYTES` and every non-conforming line is dropped. → Task 1 `test_rotation_keeps_file_bounded_and_parseable`, `test_read_is_bounded_to_tail`.
3. **Silent / unaware payload (the common case)** — arbitrary CLI workers don't know the protocol and never emit. This must read as `absent` (no data), never as death, never as `measurement_degraded`, and never feed a kill. → Task 3 `test_payload_heartbeat_absent_means_no_data`; Task 4 `test_status_reports_stale_payload_without_killing_verdict`.
4. **Wedged filesystem mid-syscall (OneDrive/SMB)** — a `read()` on a sync-deferred tree blocks *inside* the syscall; no pre-check can preempt it. The only bound is isolating the measurement in a daemon thread and abandoning it on budget expiry (runner commands are one-shot processes, so the abandoned thread dies with the exit; this residual is documented like the Popen→assign window at `delegate.py:1190-1197`). → Task 5 `test_blocking_measurement_times_out_fast`, `test_verify_refuses_closed_on_measurement_timeout`.
5. **Clock/sequence anomalies** — unparseable or missing `epoch`, non-monotonic `seq` across emitter process restarts. Staleness must return "unjudgeable" (→ degraded report), not "stale", and the dispatch-loop drain must dedupe on `epoch` so a restarted emitter doesn't double-journal. → Task 1 `test_is_stale`; Task 3 drain dedupe logic.

---

## Task 1 — `heartbeat.py`: protocol module (emitter + bounded reader)

**Files:**
- Create: `harnesses/kimi-code/delegate/heartbeat.py`
- Test: `harnesses/kimi-code/delegate/tests/test_heartbeat.py`

**Interfaces (contract every later task consumes):**

```
SCHEMA_VERSION: int = 1
MAX_HEARTBEAT_BYTES: int = 65536      # rotation cap for the append file
READ_TAIL_BYTES: int = 16384          # reader never reads more than this
NOMINAL_CADENCE_S: float = 15.0       # documented emitter target cadence
STALE_AFTER_S: float = 60.0           # default staleness threshold (reader-side)

heartbeat_path_from_env(env=None) -> str | None
    # reads DELEGATE_HEARTBEAT_PATH; env defaults to os.environ

emit(stage, sub_stage=None, counters=None, path=None, env=None) -> dict | None
    # Appends one schema-v1 record; rotates when the append would exceed
    # MAX_HEARTBEAT_BYTES; fsyncs. Returns the record, or None when no path
    # is configured or any OSError occurs. NEVER raises.

read_latest(path) -> tuple[dict | None, str]
    # Bounded tail read (last READ_TAIL_BYTES). status ∈
    # {"ok", "absent", "empty", "corrupt", "unreadable"}.
    # "corrupt" = bytes present but no valid record in the retained tail.

is_stale(record, now=None, stale_after_s=STALE_AFTER_S) -> bool | None
    # None = unjudgeable (no numeric epoch) — callers report degraded, not stale.

Record schema (one JSON object per line):
    {"v": 1, "ts": "%Y-%m-%dT%H:%M:%S" local, "epoch": float, "pid": int,
     "seq": int (monotonic per emitter process, restarts at process start),
     "dispatch_id": str | None (from DELEGATE_DISPATCH_ID),
     "stage": str, "sub_stage": str | None, "counters": dict}
```

**Steps:**

1. Write the failing test file `harnesses/kimi-code/delegate/tests/test_heartbeat.py`:

```python
#!/usr/bin/env python3
"""Payload heartbeat protocol (#105/TOOL-034): emitter, rotation, bounded reader.

Run: python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import heartbeat  # noqa: E402


class HeartbeatTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hb-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = str(Path(self.tmp) / "hb.jsonl")

    def _record(self, **over):
        rec = {"v": 1, "ts": "2026-09-25T10:00:00", "epoch": 1000.0,
               "pid": 1, "seq": 1, "dispatch_id": "d-1",
               "stage": "implement", "sub_stage": None, "counters": {}}
        rec.update(over)
        return rec


class EmitTest(HeartbeatTestBase):
    def test_emit_writes_one_schema_record(self):
        rec = heartbeat.emit("implement", sub_stage="tests",
                             counters={"tests_run": 3},
                             path=self.path, env={"DELEGATE_DISPATCH_ID": "d-1"})
        self.assertEqual(rec["v"], heartbeat.SCHEMA_VERSION)
        self.assertEqual(rec["stage"], "implement")
        self.assertEqual(rec["sub_stage"], "tests")
        self.assertEqual(rec["counters"], {"tests_run": 3})
        self.assertEqual(rec["dispatch_id"], "d-1")
        self.assertEqual(rec["pid"], os.getpid())
        self.assertIsInstance(rec["seq"], int)
        self.assertIsInstance(rec["epoch"], float)
        lines = Path(self.path).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["stage"], "implement")

    def test_seq_is_monotonic_within_process(self):
        a = heartbeat.emit("a", path=self.path)
        b = heartbeat.emit("b", path=self.path)
        self.assertGreater(b["seq"], a["seq"])

    def test_emit_uses_env_path_and_returns_none_without_one(self):
        rec = heartbeat.emit("intake", env={"DELEGATE_HEARTBEAT_PATH": self.path})
        self.assertIsNotNone(rec)
        self.assertTrue(Path(self.path).is_file())
        self.assertIsNone(heartbeat.emit("intake", env={}))

    def test_emit_never_raises_on_io_failure(self):
        blocker = Path(self.tmp) / "blocker"
        blocker.write_text("x", encoding="utf-8")
        # path's parent component is a FILE: makedirs/open must fail.
        self.assertIsNone(heartbeat.emit("s", path=str(blocker / "hb.jsonl")))

    def test_rotation_keeps_file_bounded_and_parseable(self):
        pad = "x" * 1000
        for i in range(200):  # ~210 KB of appends against a 64 KiB cap
            heartbeat.emit(f"stage-{i}", counters={"pad": pad}, path=self.path)
        size = os.path.getsize(self.path)
        self.assertLessEqual(size, heartbeat.MAX_HEARTBEAT_BYTES + 2048)
        rec, status = heartbeat.read_latest(self.path)
        self.assertEqual(status, "ok")
        self.assertEqual(rec["stage"], "stage-199")


class ReadLatestTest(HeartbeatTestBase):
    def test_absent(self):
        rec, status = heartbeat.read_latest(str(Path(self.tmp) / "nope.jsonl"))
        self.assertEqual((rec, status), (None, "absent"))

    def test_empty(self):
        Path(self.path).write_text("", encoding="utf-8")
        self.assertEqual(heartbeat.read_latest(self.path), (None, "empty"))

    def test_torn_tail_is_skipped(self):
        good = self._record(stage="implement")
        Path(self.path).write_text(json.dumps(good) + "\n" + '{"v": 1, "sta',
                                   encoding="utf-8")
        rec, status = heartbeat.read_latest(self.path)
        self.assertEqual(status, "ok")
        self.assertEqual(rec["stage"], "implement")

    def test_all_garbage_is_corrupt(self):
        Path(self.path).write_text('garbage\n{"v":1\n', encoding="utf-8")
        self.assertEqual(heartbeat.read_latest(self.path), (None, "corrupt"))

    def test_unreadable(self):
        # NOTE: a FILE as a parent path component raises FileNotFoundError
        # on Windows (verified 3.10/3.14: ERROR_PATH_NOT_FOUND maps to FNF),
        # which correctly reads as "absent" — so the unreadable branch is
        # forced with a patched getsize raising a non-FNF OSError.
        Path(self.path).write_text("{}\n", encoding="utf-8")
        with mock.patch.object(
                heartbeat.os.path, "getsize",
                side_effect=PermissionError("denied")):
            rec, status = heartbeat.read_latest(self.path)
        self.assertEqual((rec, status), (None, "unreadable"))

    def test_read_is_bounded_to_tail(self):
        good = self._record(stage="verify")
        pad = "g" * (heartbeat.READ_TAIL_BYTES * 2)
        Path(self.path).write_text(pad + "\n" + json.dumps(good) + "\n",
                                   encoding="utf-8")
        rec, status = heartbeat.read_latest(self.path)
        self.assertEqual(status, "ok")
        self.assertEqual(rec["stage"], "verify")

    def test_is_stale(self):
        now = time.time()
        self.assertTrue(heartbeat.is_stale({"epoch": now - 3600}, now=now))
        self.assertFalse(heartbeat.is_stale({"epoch": now}, now=now))
        # Unjudgeable freshness is None, not True: callers degrade, not stale.
        self.assertIsNone(heartbeat.is_stale({"epoch": "not-a-number"}, now=now))
        self.assertIsNone(heartbeat.is_stale(None))


if __name__ == "__main__":
    unittest.main()
```

2. Run it, expect failure (module does not exist):

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/delegate/tests -v -k heartbeat
```
Expect: `ModuleNotFoundError: No module named 'heartbeat'` (collection error).

3. Implement `harnesses/kimi-code/delegate/heartbeat.py`:

```python
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
silence beyond STALE_AFTER_S as "stale" (reported) — never as death; kill
predicates remain wall-clock-only until #106.

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
```

4. Run, expect pass:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
```
Expect: all tests pass including the 11 new `heartbeat` tests, and the pre-existing delegate/breakaway/catalog/model_dispatch suites stay green.

5. Commit (surface for user confirmation per `AGENTS.md:120`):

```
cd mp_wt && git add harnesses/kimi-code/delegate/heartbeat.py harnesses/kimi-code/delegate/tests/test_heartbeat.py && git commit -m "feat(delegate): payload heartbeat protocol module, emitter + bounded reader (#105)"
```

---

## Task 2 — Delegate CLI flags + environment injection

**Files:**
- Modify: `harnesses/kimi-code/delegate/delegate.py`
- Modify: `harnesses/kimi-code/delegate/tests/test_delegate.py` (test helper only)
- Test: `harnesses/kimi-code/delegate/tests/test_heartbeat.py` (append the class below)

**Interfaces:**

```
delegate.py CLI gains:
  --dispatch-id <str>      (default None)
  --heartbeat-file <path>  (default None)

Child environment gains (injected by the parent, never inherited — same
doctrine as _CHILD_MARKER at delegate.py:1148-1152):
  DELEGATE_DISPATCH_ID     = args.dispatch_id      (only when flag given)
  DELEGATE_HEARTBEAT_PATH  = args.heartbeat_file   (only when flag given)

test_delegate.run_delegate(..., extra_argv=())   # helper widened
DelegateTestBase._run(..., extra_argv=())        # passthrough widened
```

**Steps:**

1. Append the failing test to `harnesses/kimi-code/delegate/tests/test_heartbeat.py` (after `ReadLatestTest`, before the `__main__` guard). This mirrors `test_breakaway.py`'s import of `DelegateTestBase` (`test_breakaway.py:20-21`):

```python
from test_delegate import DelegateTestBase, make_agent  # noqa: E402

_ENV_DUMP = (
    "import os, pathlib\n"
    "pathlib.Path('env_dump.txt').write_text(\n"
    "    (os.environ.get('DELEGATE_HEARTBEAT_PATH') or '<absent>') + '\\n' +\n"
    "    (os.environ.get('DELEGATE_DISPATCH_ID') or '<absent>'),\n"
    "    encoding='utf-8')\n"
)


class TestHeartbeatEnvInjection(DelegateTestBase):
    """#105: the delegate injects the heartbeat side-channel coordinates
    into the child env when (and only when) the runner passes the flags."""

    def setUp(self):
        super().setUp()
        dump_script = self._script("env_dump", _ENV_DUMP)
        self.config_path = self._config({"test-agent": make_agent(dump_script)})

    def test_heartbeat_env_injected_when_flags_passed(self):
        hb = str(Path(self.tmpdir) / "hb.jsonl")
        out, err, rc = self._run("test-agent", task="hello",
                                 extra_argv=["--heartbeat-file", hb,
                                             "--dispatch-id", "d-42"])
        self._assert_result(out, err, rc, "completed", 0)
        dumped = (Path(self.workspace) / "env_dump.txt").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(dumped, [hb, "d-42"])

    def test_no_flags_no_env(self):
        out, err, rc = self._run("test-agent", task="hello")
        self._assert_result(out, err, rc, "completed", 0)
        dumped = (Path(self.workspace) / "env_dump.txt").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(dumped, ["<absent>", "<absent>"])
```

(The dump lands in `self.workspace` because the child's cwd is the workspace, `delegate.py:1175`.)

2. Run, expect failure — two distinct failures at this point:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/delegate/tests -v -k heartbeat
```
Expect: `TypeError: DelegateTestBase._run() got an unexpected keyword argument 'extra_argv'`.

3. Widen the test helper in `harnesses/kimi-code/delegate/tests/test_delegate.py`. At `:279-301` replace the `run_delegate` signature and argv assembly:

```python
def run_delegate(agent, workspace, task=None, task_file=None, timeout=None,
                 config_path=None, timeout_wrap=120, resume_from=None,
                 extra_argv=()):
    argv = [sys.executable, str(_DELEGATE_DIR / "delegate.py"),
            "--agent", agent, "--workspace", workspace]
    if task is not None:
        argv += ["--task", task]
    if task_file is not None:
        argv += ["--task-file", task_file]
    if timeout is not None:
        argv += ["--timeout", str(timeout)]
    if resume_from is not None:
        argv += ["--resume-from", resume_from]
    argv += list(extra_argv)
    env = dict(os.environ)
    if config_path:
        env["DELEGATE_CONFIG"] = config_path
    proc = subprocess.run(argv, capture_output=True, env=env, timeout=timeout_wrap)
    return (proc.stdout.decode("utf-8", errors="replace"),
            proc.stderr.decode("utf-8", errors="replace"),
            proc.returncode)
```

And widen `DelegateTestBase._run` at `:378-385`:

```python
    def _run(self, agent, ws=None, task=None, task_file=None, timeout=None, config=None,
             timeout_wrap=120, resume_from=None, extra_argv=()):
        return run_delegate(
            agent, ws or self.workspace,
            task=task, task_file=task_file, timeout=timeout,
            config_path=config or self.config_path,
            timeout_wrap=timeout_wrap, resume_from=resume_from,
            extra_argv=extra_argv,
        )
```

Re-run the heartbeat tests; expect a NEW failure: the child writes `<absent>` / `<absent>` in the flags-passed case (argparse accepts nothing yet — actually it exits `invalid` with "Invalid command-line arguments", so `_assert_result` fails on status `invalid` vs `completed`).

4. Implement the delegate changes.

(a) CLI flags — in `main()`, immediately after the `--resume-from` argument (`delegate.py:1478-1480`):

```python
    parser.add_argument("--dispatch-id", dest="dispatch_id", default=None,
                        help="Runner-minted dispatch id (#105); injected into "
                             "the child environment for heartbeat correlation")
    parser.add_argument("--heartbeat-file", dest="heartbeat_file", default=None,
                        help="Path the payload appends progress heartbeats to "
                             "(#105); injected as DELEGATE_HEARTBEAT_PATH")
```

(b) Environment injection — immediately after `child_env[_CHILD_MARKER] = "1"` (`delegate.py:1152`):

```python
    # #105 (TOOL-034): payload progress side channel. INJECTED like the
    # nesting marker and KIMI_CODE_HOME above — the agent allowlist governs
    # inheritance, not wrapper invariants. No flag -> no variable -> the
    # payload simply never emits, which readers report as "absent".
    if getattr(args, "heartbeat_file", None):
        child_env["DELEGATE_HEARTBEAT_PATH"] = args.heartbeat_file
    if getattr(args, "dispatch_id", None):
        child_env["DELEGATE_DISPATCH_ID"] = args.dispatch_id
```

5. Run, expect pass:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
```
Expect: both new injection tests pass; entire delegate suite green.

6. Commit:

```
cd mp_wt && git add harnesses/kimi-code/delegate/delegate.py harnesses/kimi-code/delegate/tests/test_delegate.py harnesses/kimi-code/delegate/tests/test_heartbeat.py && git commit -m "feat(delegate): inject DELEGATE_HEARTBEAT_PATH/DELEGATE_DISPATCH_ID from --heartbeat-file/--dispatch-id (#105)"
```

---

## Task 3 — Runner dispatch wiring: forward the flags, drain progress into the journal

**Files:**
- Modify: `harnesses/kimi-code/runner/runner.py`
- Modify: `harnesses/kimi-code/runner/tests/fake_worker.py`
- Test: `harnesses/kimi-code/runner/tests/test_dispatch_journal.py` (append tests)

**Interfaces:**

```
runner._heartbeat_module() -> module | None
    # Two-layout resolution of delegate/heartbeat.py, FAIL-SOFT (#109):
    # candidates [here/"heartbeat.py", here.parent/"delegate"/"heartbeat.py"]
    # (flat install vs repo layout, mirroring _task_schema at runner.py:161-190).
    # None = unmeasurable, abstain. Cached in _HEARTBEAT_MOD.

runner._heartbeat_file(root, dispatch_id) -> Path
    # <state>/heartbeats/<dispatch_id>.jsonl — runner-state adjacent, local fs.

runner.run_delegate(delegate_py, agent, ws, prompt, timeout_s,
                    on_heartbeat=None, dispatch_id=None, heartbeat_file=None)
    # forwards --dispatch-id/--heartbeat-file verbatim when not None.

Journal gains event "dispatch_progress":
    {"event": "dispatch_progress", "dispatch_id": str, "payload_seq": int|None,
     "payload_pid": int|None, "stage": str|None, "sub_stage": str|None,
     "counters": dict, "payload_ts": str|None}   (+ journal_seq/at from _journal_append)

Journal "dispatch_finished" gains (additive, existing keys unchanged):
    "payload_heartbeats": int          # count of dispatch_progress records
    "payload_heartbeat_status": str    # final read_latest status | "unavailable"

fake_worker.py gains CLI args --dispatch-id / --heartbeat-file and env knobs:
    FAKE_WORKER_HEARTBEAT=1  -> append two schema-v1 heartbeat lines to
                                --heartbeat-file: "intake" before the sleep,
                                "implement" after it, then the envelope
    FAKE_WORKER_SLEEP=<s>    -> sleep <s> seconds between the two beats
                                (or before the envelope when not emitting)
```

**Steps:**

1. Append the failing tests to `harnesses/kimi-code/runner/tests/test_dispatch_journal.py` (inside `DispatchJournalTest`, after the ack test at `:152-169`):

```python
    # ── #105: payload-emitted progress lands in the journal ──────────────
    def test_payload_heartbeat_is_journaled(self):
        # FAKE_WORKER_SLEEP=11 spans one 10s runner heartbeat tick, so the
        # dispatch-loop drain fires mid-run; the final drain dedupes.
        # (One ~11s test; the honest cost of exercising the real poll loop.)
        ws, task, sdir = self._setup_ready()
        env = {"FAKE_WORKER_HEARTBEAT": "1", "FAKE_WORKER_SLEEP": "11"}
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_WORKER), env_extra=env)
        self.assertEqual(rc, 0, out)
        prog = [e for e in journal_lines(sdir)
                if e["event"] == "dispatch_progress"]
        self.assertTrue(prog, "expected dispatch_progress records")
        self.assertEqual(prog[0]["stage"], "intake")
        stages = {e["stage"] for e in prog}
        self.assertEqual(stages, {"intake", "implement"})
        fin = [e for e in journal_lines(sdir)
               if e["event"] == "dispatch_finished"]
        self.assertEqual(fin[0]["payload_heartbeats"], len(prog))
        self.assertEqual(fin[0]["payload_heartbeat_status"], "ok")
        # Runner-generated heartbeats are untouched (the liveness floor).
        beats = [e for e in journal_lines(sdir)
                 if e["event"] == "dispatch_heartbeat"]
        self.assertTrue(beats, "runner heartbeat cadence must not regress")

    def test_payload_heartbeat_absent_means_no_data(self):
        ws, task, sdir = self._setup_ready()
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_WORKER))
        self.assertEqual(rc, 0, out)
        self.assertEqual([e for e in journal_lines(sdir)
                          if e["event"] == "dispatch_progress"], [])
        fin = [e for e in journal_lines(sdir)
               if e["event"] == "dispatch_finished"]
        self.assertEqual(fin[0]["payload_heartbeats"], 0)
        self.assertEqual(fin[0]["payload_heartbeat_status"], "absent")
```

2. Run, expect failure:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```
Expect: `test_payload_heartbeat_is_journaled` fails on the empty `prog` list (and, once flags are forwarded before `fake_worker` is updated, `fake_worker` argparse errors — that ordering is why step 3 implements fake_worker first).

3. Implement.

(a) `harnesses/kimi-code/runner/tests/fake_worker.py` — add `import time` (after `import sys`), widen argparse, and add emission before the mode/envelope block:

```python
    parser.add_argument("--dispatch-id", default=None)
    parser.add_argument("--heartbeat-file", default=None)
```

```python
    # #105: the runner forwards --heartbeat-file/--dispatch-id verbatim to
    # whatever --delegate names. The fake plays delegate AND payload in one:
    # with FAKE_WORKER_HEARTBEAT=1 it appends schema-v1 heartbeat lines —
    # "intake" BEFORE the sleep and "implement" AFTER, so a sleep spanning
    # one 10s runner tick lets the mid-run drain and the final drain each
    # observe a distinct record (read_latest returns only the newest).
    sleep_s = float(os.environ.get("FAKE_WORKER_SLEEP", "0") or 0)
    if args.heartbeat_file and os.environ.get("FAKE_WORKER_HEARTBEAT"):
        hb = Path(args.heartbeat_file)
        hb.parent.mkdir(parents=True, exist_ok=True)

        def _beat(stage, seq):
            now = time.time()
            with open(hb, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "v": 1,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S",
                                        time.localtime(now)),
                    "epoch": now, "pid": os.getpid(), "seq": seq,
                    "dispatch_id": args.dispatch_id,
                    "stage": stage, "sub_stage": None, "counters": {},
                }) + "\n")

        _beat("intake", 1)
        if sleep_s > 0:
            time.sleep(sleep_s)
        _beat("implement", 2)
    elif sleep_s > 0:
        time.sleep(sleep_s)
```

(b) `harnesses/kimi-code/runner/runner.py` — add the resolver and path helper immediately after `_task_schema()` (after `:190`):

```python
_HEARTBEAT_MOD = "unset"


def _heartbeat_module():
    """delegate/heartbeat.py — the payload progress protocol (#105/TOOL-034).

    Same two-layout resolution as _task_schema (repo: sibling
    harnesses/kimi-code/delegate/; flat install: a sibling file), but
    FAIL-SOFT per the #109 doctrine: an unresolvable or unimportable module
    means payload progress is unmeasurable — abstain (None), never refuse.
    """
    global _HEARTBEAT_MOD
    if _HEARTBEAT_MOD == "unset":
        import importlib.util
        here = Path(__file__).resolve().parent
        _HEARTBEAT_MOD = None
        for cand in (here / "heartbeat.py",
                     here.parent / "delegate" / "heartbeat.py"):
            if cand.is_file():
                spec = importlib.util.spec_from_file_location("heartbeat",
                                                              str(cand))
                mod = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(mod)
                except Exception:
                    break  # unimportable == unmeasurable: abstain
                _HEARTBEAT_MOD = mod
                break
    return _HEARTBEAT_MOD


def _heartbeat_file(root, dispatch_id):
    """Payload heartbeat side channel: <state>/heartbeats/<dispatch_id>.jsonl.

    Lives with the runner state (outside the workspace, local fs) so monitor
    reads never touch worker-writable or sync-deferred trees (#109)."""
    return Path(root) / "heartbeats" / f"{dispatch_id}.jsonl"
```

(c) `run_delegate` (`runner.py:708`) — widen the signature and forward the flags. Replace the signature line and the `cmd` construction (`:723-724`):

```python
def run_delegate(delegate_py, agent, ws, prompt, timeout_s, on_heartbeat=None,
                 dispatch_id=None, heartbeat_file=None):
```

```python
        cmd = [sys.executable, delegate_py, "--agent", agent, "--workspace", str(ws),
               "--task-file", task_file, "--timeout", str(timeout_s)]
        # #105: forwarded verbatim; the delegate injects them into the child
        # env. Whatever --delegate names must tolerate these two flags.
        if heartbeat_file is not None:
            cmd += ["--heartbeat-file", str(heartbeat_file)]
        if dispatch_id is not None:
            cmd += ["--dispatch-id", dispatch_id]
```

Also extend the docstring (`:709-716`) with one line: `#105: dispatch_id/heartbeat_file wire the payload progress side channel; both are forwarded verbatim and the heartbeat file is drained by the caller's on_heartbeat.`

(d) `cmd_dispatch` — replace the heartbeat closure block (`runner.py:987-1001`) with:

```python
    heartbeat_count = [0]
    # #105: payload progress drain state. The heartbeat file is runner-owned
    # and local; reads are bounded and every failure downgrades to a status
    # string (#109: abstain + report — this never raises, never kills).
    hb_file = _heartbeat_file(sroot, dispatch_id)
    hb_file.parent.mkdir(parents=True, exist_ok=True)
    hb = _heartbeat_module()
    progress = {"count": 0, "last_epoch": None,
                "status": "unavailable" if hb is None else "absent"}

    def _drain_payload_progress():
        if hb is None:
            return
        try:
            rec, status = hb.read_latest(str(hb_file))
        except Exception:
            progress["status"] = "unreadable"
            return
        progress["status"] = status
        if rec is None:
            return
        epoch = rec.get("epoch")
        if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
            if progress["last_epoch"] is not None and epoch <= progress["last_epoch"]:
                return  # already journaled (dedupe across drains)
            progress["last_epoch"] = epoch
        progress["count"] += 1
        _journal_append(sroot, {
            "event": "dispatch_progress", "dispatch_id": dispatch_id,
            "payload_seq": rec.get("seq"), "payload_pid": rec.get("pid"),
            "stage": rec.get("stage"), "sub_stage": rec.get("sub_stage"),
            "counters": rec.get("counters") or {},
            "payload_ts": rec.get("ts"),
        })

    def _heartbeat():
        # A5 (#73): alive-but-slow vs dead must be distinguishable within one
        # heartbeat interval, not one full timeout.
        heartbeat_count[0] += 1
        _journal_append(sroot, {
            "event": "dispatch_heartbeat", "dispatch_id": dispatch_id,
            "beat": heartbeat_count[0],
        })
        _drain_payload_progress()

    t0 = time.monotonic()
    envelope = run_delegate(delegate_py, agent, ws, task["prompt"],
                            state["budget"]["timeout_s"],
                            on_heartbeat=_heartbeat, dispatch_id=dispatch_id,
                            heartbeat_file=str(hb_file))
```

(e) `cmd_dispatch` — the `dispatch_finished` record (`runner.py:1024-1030`) gains the additive fields, preceded by a final drain (a fast worker exits before any 10s tick, so without this its heartbeats would never be journaled):

```python
    _drain_payload_progress()  # final drain: fast workers beat the 10s tick
    _journal_append(sroot, {
        "event": "dispatch_finished", "dispatch_id": dispatch_id,
        "task_id": state["task_id"], "dispatch_seq": dispatch_seq,
        "agent": agent, "envelope_status": envelope_status,
        "duration_seconds": round(envelope.get("duration_seconds", wall), 3),
        "heartbeats": heartbeat_count[0],
        # #105: additive payload-progress summary (existing keys unchanged).
        "payload_heartbeats": progress["count"],
        "payload_heartbeat_status": progress["status"],
    })
```

4. Run, expect pass:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```
Expect: both new tests pass (~11s for the sleep one); all existing runner suites green (`dispatch_finished` field additions are additive, and `test_journal_records_open_and_finished` at `:106-120` indexes events, not fields).

5. Commit:

```
cd mp_wt && git add harnesses/kimi-code/runner/runner.py harnesses/kimi-code/runner/tests/fake_worker.py harnesses/kimi-code/runner/tests/test_dispatch_journal.py && git commit -m "feat(runner): drain payload heartbeats into dispatch_progress journal records (#105)"
```

---

## Task 4 — `cmd_status`: payload progress surface + `measurement_degraded`

**Files:**
- Modify: `harnesses/kimi-code/runner/runner.py`
- Test: `harnesses/kimi-code/runner/tests/test_dispatch_journal.py` (append tests)

**Interfaces:**

```
runner._payload_progress(sroot, open_ids) -> tuple[dict, bool]
    # {dispatch_id: {"status": "ok"|"stale"|"absent"|"empty"|"corrupt"|
    #                "unreadable"|"unavailable",
    #                "stage"?, "sub_stage"?, "counters"?, "pid"?,
    #                "age_seconds"?}}, degraded: bool
    # Never raises. "stale"/"absent"/"empty" are NOT degradation; a payload
    # that never emits or goes quiet is data, not broken measurement.
    # "corrupt"/"unreadable"/"unavailable" and unjudgeable epochs ARE.

cmd_status output gains (top level, before the **state spread):
    "payload_progress": <dict from _payload_progress>
    "measurement_degraded": bool
    "degraded_components": list[str]   # ["payload_progress"] or []
```

**Steps:**

1. Append the failing tests to `test_dispatch_journal.py` (inside `DispatchJournalTest`):

```python
    # ── #105/#109: status surfaces payload progress, degrades, never refuses ──
    def _open_with_heartbeat(self, hb_text):
        """Hand-write a fresh open dispatch plus its heartbeat file (the
        crash-signature idiom from test_orphaned_open_dispatch_is_surfaced)."""
        ws, task, sdir = self._setup_ready()
        started = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        (sdir / "journal.jsonl").write_text(
            json.dumps({"journal_seq": 0, "event": "dispatch_open",
                        "task_id": "t1", "dispatch_id": "live-1",
                        "dispatch_seq": 0, "agent": "glm-worker",
                        "timeout_s": 60, "runner_pid": 999999,
                        "at": started}) + "\n",
            encoding="utf-8")
        hb_dir = sdir / "heartbeats"
        hb_dir.mkdir()
        (hb_dir / "live-1.jsonl").write_text(hb_text, encoding="utf-8")
        return ws

    def test_status_reports_payload_progress(self):
        now = time.time()
        ws = self._open_with_heartbeat(json.dumps({
            "v": 1, "ts": time.strftime("%Y-%m-%dT%H:%M:%S",
                                        time.localtime(now)),
            "epoch": now, "pid": 4321, "seq": 3, "dispatch_id": "live-1",
            "stage": "verify", "sub_stage": None,
            "counters": {"tests_run": 12}}) + "\n")
        rc, out = run_runner("status", "--workspace", ws)
        self.assertEqual(rc, 0, out)
        p = out["payload_progress"]["live-1"]
        self.assertEqual(p["status"], "ok")
        self.assertEqual(p["stage"], "verify")
        self.assertEqual(p["counters"], {"tests_run": 12})
        self.assertLess(p["age_seconds"], 60)
        self.assertFalse(out["measurement_degraded"])
        self.assertEqual(out["degraded_components"], [])

    def test_status_degrades_on_corrupt_heartbeat(self):
        ws = self._open_with_heartbeat('garbage\n{"v":1\n')
        rc, out = run_runner("status", "--workspace", ws)
        # Abstain + report: exit 0, verdict present, degradation named.
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["payload_progress"]["live-1"]["status"], "corrupt")
        self.assertTrue(out["measurement_degraded"])
        self.assertIn("payload_progress", out["degraded_components"])

    def test_status_reports_stale_payload_without_killing_verdict(self):
        old = time.time() - 3600
        ws = self._open_with_heartbeat(json.dumps({
            "v": 1, "ts": time.strftime("%Y-%m-%dT%H:%M:%S",
                                        time.localtime(old)),
            "epoch": old, "pid": 4321, "seq": 1, "dispatch_id": "live-1",
            "stage": "implement", "sub_stage": None, "counters": {}}) + "\n")
        rc, out = run_runner("status", "--workspace", ws)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["payload_progress"]["live-1"]["status"], "stale")
        # Silence is a reported signal, NOT broken measurement and NOT death.
        self.assertFalse(out["measurement_degraded"])
```

2. Run, expect failure:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v -k status
```
Expect: `KeyError: 'payload_progress'` in all three new tests.

3. Implement in `harnesses/kimi-code/runner/runner.py`.

(a) Add `_payload_progress` immediately after `_ts_to_epoch` (`:593-598`):

```python
def _payload_progress(sroot, open_ids):
    """#105/#109: payload-emitted progress for open dispatches.

    Monitor-path rule (#109): read ONLY runner-state and payload-emitted
    files; every failure downgrades to a status string; this function never
    raises and its output never feeds a kill or refusal. "stale"/"absent" are
    payload signals; "corrupt"/"unreadable"/"unavailable" and unjudgeable
    epochs are degraded MEASUREMENT (the bool)."""
    hb = _heartbeat_module()
    if hb is None:
        return {did: {"status": "unavailable"} for did in sorted(open_ids)}, \
            bool(open_ids)
    out, degraded = {}, False
    for did in sorted(open_ids):
        try:
            rec, status = hb.read_latest(str(_heartbeat_file(sroot, did)))
        except Exception:
            rec, status = None, "unreadable"
        entry = {"status": status}
        if rec is not None:
            entry.update({
                "stage": rec.get("stage"),
                "sub_stage": rec.get("sub_stage"),
                "counters": rec.get("counters") or {},
                "pid": rec.get("pid"),
            })
            epoch = rec.get("epoch")
            if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
                entry["age_seconds"] = round(time.time() - epoch)
                if hb.is_stale(rec):
                    entry["status"] = "stale"
            else:
                degraded = True  # freshness unjudgeable = degraded measurement
        if status in ("unreadable", "corrupt"):
            degraded = True
        out[did] = entry
    return out, degraded
```

(b) In `cmd_status`, immediately after the orphans computation (`:1530-1534`):

```python
    progress, progress_degraded = _payload_progress(sroot, open_dispatches)
```

and in the `out` dict, immediately before the `**state` spread (`:1574`), add:

```python
        # #105/#109: payload-emitted progress for open dispatches. Monitor
        # path: degradation is reported, never refused on, never killed on.
        "payload_progress": progress,
        "measurement_degraded": progress_degraded,
        "degraded_components": (["payload_progress"]
                                if progress_degraded else []),
```

4. Run, expect pass:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```
Expect: the three new status tests pass; all existing runner suites green (the status output additions are new keys; `**state` spread is unaffected).

5. Commit:

```
cd mp_wt && git add harnesses/kimi-code/runner/runner.py harnesses/kimi-code/runner/tests/test_dispatch_journal.py && git commit -m "feat(runner): status surfaces payload progress with abstain-and-report degradation (#105 #109)"
```

---

## Task 5 — Bounded workspace measurement (#109): the concrete observer-wedge fix

**Files:**
- Modify: `harnesses/kimi-code/runner/runner.py`
- Test: `harnesses/kimi-code/runner/tests/test_measurement_isolation.py` (new)

**Interfaces:**

```
runner.MEASUREMENT_BUDGET_S: float = 120.0
    # Wall-clock budget for one workspace-tree measurement. Acceptance-path
    # bound, not a liveness signal.

class runner.MeasurementTimeout(Exception)
    # .what: str — which measurement expired.

runner._measure_with_deadline(fn, budget_s, what)
    # Runs fn() in a daemon thread; join(budget_s); alive-after-join ->
    # raise MeasurementTimeout(what); fn's exception propagates; else result.

cmd_verify refuses CLOSED on MeasurementTimeout:
    writes receipt {"passed": False, "rejected": "measurement_timeout", ...}
    and exits 1 (mirrors the verifier_timeout receipt at runner.py:1137-1155).
cmd_init refuses: {"error": "measurement_timeout", ...}, exit 1.
cmd_accept refuses: {"accepted": False, "reason": "measurement_timeout: ..."}, exit 1.
```

**Steps:**

1. Write the failing test file `harnesses/kimi-code/runner/tests/test_measurement_isolation.py`. Runner tests elsewhere are subprocess-driven; this file imports `runner` in-process (the module is import-safe — `if __name__ == "__main__"` guard at `runner.py:1651`) so the wedge can be simulated as a blocking function rather than a real wedged filesystem, and so monitor isolation can be pinned with a bomb:

```python
#!/usr/bin/env python3
"""#109/TOOL-038: bounded workspace measurement + monitor isolation.

In-process on purpose: the wedge analog is a function that never returns
(a read syscall blocked on a dead OneDrive/SMB mount), and the only bound
for that class is the daemon-thread deadline — no pre-check can preempt a
blocked syscall. Also pins that monitor paths NEVER call tree measurement.

Run: python -m unittest discover -s harnesses/kimi-code/runner/tests -v
"""

import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runner"))

import runner  # noqa: E402

FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15, f"sum_to_n(5)={sum_to_n(5)}, expected 15"\n'
    'print("PASS")\n'
)


def _ns(**kw):
    defaults = dict(workspace=None, task=None, state_dir=None, reinit=False,
                    reset_provider_gate=False, agent_map=None, delegate=None,
                    ack=None, allow_zero_dispatch=False)
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def _run_cmd(fn, ns):
    """Call a runner cmd_* in-process; returns (exit_code, parsed_json)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = fn(ns)
        except SystemExit as e:
            rc = e.code
    return rc, json.loads(buf.getvalue())


class MeasureWithDeadlineTest(unittest.TestCase):
    def test_returns_result(self):
        self.assertEqual(
            runner._measure_with_deadline(lambda: 42, 5, "t"), 42)

    def test_propagates_exceptions(self):
        def boom():
            raise ValueError("nope")
        with self.assertRaises(ValueError):
            runner._measure_with_deadline(boom, 5, "t")

    def test_blocking_measurement_times_out_fast(self):
        t0 = time.monotonic()
        with self.assertRaises(runner.MeasurementTimeout) as cm:
            runner._measure_with_deadline(lambda: time.sleep(30), 0.2,
                                          "wedged-tree")
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(cm.exception.what, "wedged-tree")


class AcceptancePathsFailClosedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-mi-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        ws = Path(self.tmp) / "ws"
        ws.mkdir()
        (ws / "math_utils.py").write_text(FIXED, encoding="utf-8")
        (ws / "check.py").write_text(CHECK, encoding="utf-8")
        self.ws = str(ws)
        task = {"task_id": "t1", "prompt": "Fix the bug.",
                "features": {"bounded": True, "known_location": True,
                             "objective_acceptance": True},
                "scope": ["math_utils.py"],
                "verifier": {"argv": ["{python}", "check.py"]},
                "budget": {"max_dispatches": 4, "max_stagnant": 3,
                           "timeout_s": 60}}
        tp = Path(self.tmp) / "task.json"
        tp.write_text(json.dumps(task), encoding="utf-8")
        self.task = str(tp)
        rc, out = _run_cmd(runner.cmd_init,
                           _ns(workspace=self.ws, task=self.task))
        assert rc == 0, out

    def _wedged_tree_signature(self):
        orig_sig, orig_budget = runner.tree_signature, runner.MEASUREMENT_BUDGET_S

        class _Wedge:
            def __enter__(self):
                runner.tree_signature = lambda ws: time.sleep(30)
                runner.MEASUREMENT_BUDGET_S = 0.2

            def __exit__(self, *exc):
                runner.tree_signature = orig_sig
                runner.MEASUREMENT_BUDGET_S = orig_budget
        return _Wedge()

    def test_verify_refuses_closed_on_measurement_timeout(self):
        with self._wedged_tree_signature():
            rc, out = _run_cmd(runner.cmd_verify,
                               _ns(workspace=self.ws, task=self.task))
        self.assertEqual(rc, 1)
        self.assertEqual(out["passed"], False)
        self.assertEqual(out["rejected"], "measurement_timeout")

    def test_accept_refuses_on_measurement_timeout(self):
        rc, out = _run_cmd(runner.cmd_verify,
                           _ns(workspace=self.ws, task=self.task))
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["passed"], out)
        with self._wedged_tree_signature():
            rc, out = _run_cmd(runner.cmd_accept,
                               _ns(workspace=self.ws, task=self.task))
        self.assertEqual(rc, 1)
        self.assertEqual(out["accepted"], False)
        self.assertTrue(out["reason"].startswith("measurement_timeout"), out)

    def test_init_refuses_on_measurement_timeout(self):
        ws2 = Path(self.tmp) / "ws2"
        shutil.copytree(self.ws, ws2)
        with self._wedged_tree_signature():
            rc, out = _run_cmd(runner.cmd_init,
                               _ns(workspace=str(ws2), task=self.task))
        self.assertEqual(rc, 1)
        self.assertEqual(out["error"], "measurement_timeout")


class MonitorIsolationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-mon-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        ws = Path(self.tmp) / "ws"
        ws.mkdir()
        (ws / "math_utils.py").write_text(FIXED, encoding="utf-8")
        (ws / "check.py").write_text(CHECK, encoding="utf-8")
        self.ws = str(ws)
        task = {"task_id": "t1", "prompt": "Fix the bug.",
                "features": {"bounded": True, "known_location": True,
                             "objective_acceptance": True},
                "scope": ["math_utils.py"],
                "verifier": {"argv": ["{python}", "check.py"]},
                "budget": {"max_dispatches": 4, "max_stagnant": 3,
                           "timeout_s": 60}}
        tp = Path(self.tmp) / "task.json"
        tp.write_text(json.dumps(task), encoding="utf-8")
        self.task = str(tp)
        rc, out = _run_cmd(runner.cmd_init,
                           _ns(workspace=self.ws, task=self.task))
        assert rc == 0, out

    def test_status_never_measures_the_tree(self):
        def bomb(ws):
            raise AssertionError("monitor path touched the workspace tree")
        orig_sig, orig_surface = runner.tree_signature, runner.config_surface
        runner.tree_signature = bomb
        runner.config_surface = bomb
        try:
            rc, out = _run_cmd(runner.cmd_status, _ns(workspace=self.ws))
        finally:
            runner.tree_signature = orig_sig
            runner.config_surface = orig_surface
        self.assertEqual(rc, 0, out)
        self.assertIn("payload_progress", out)
        self.assertIn("measurement_degraded", out)
        self.assertIn("degraded_components", out)


if __name__ == "__main__":
    unittest.main()
```

2. Run, expect failure:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v -k measurement
```
Expect: `AttributeError: module 'runner' has no attribute '_measure_with_deadline'` (and `MeasurementTimeout`).

3. Implement in `harnesses/kimi-code/runner/runner.py`.

(a) Add `import threading` to the import block (alphabetical: between `import tempfile` and `import time`, `:68-69`).

(b) Immediately after `_sha256_file` (`:148-149`), add the doctrine block, constant, exception, and helper:

```python
# ── measurement doctrine (#109 / TOOL-038) ──────────────────────────────
# Two path classes, two failure semantics:
#   MONITOR paths (cmd_status, the dispatch-loop progress drain, the accept
#     in-flight gate, the orphan sweep) read ONLY runner-state files and
#     payload-emitted heartbeat files on local disk. A failed or degraded
#     measurement = ABSTAIN: report measurement_degraded naming the
#     component, exit 0, and NEVER kill or refuse on a measurement that
#     could not be taken.
#   ACCEPTANCE paths (cmd_init baselining, cmd_verify receipts, cmd_accept
#     staleness checks) FAIL CLOSED: a measurement that cannot finish within
#     MEASUREMENT_BUDGET_S refuses the acceptance action. The refusal IS the
#     abstention — no certificate is issued over an unmeasured tree.
# _git_toplevel's None-abstain (:320-329) is not a violation of this: it
# selects the files: manifest branch of tree_signature, which is itself a
# full measurement. _porcelain_entries fails closed (:357-363) and serves
# acceptance paths only, which this doctrine makes explicit.

MEASUREMENT_BUDGET_S = 120.0


class MeasurementTimeout(Exception):
    """A workspace measurement exceeded its wall-clock budget (#109)."""

    def __init__(self, what):
        super().__init__(f"measurement timeout: {what}")
        self.what = what


def _measure_with_deadline(fn, budget_s, what):
    """Run fn() in a daemon thread under a wall-clock budget.

    Why a thread and not a pre-check: a wedged tree (OneDrive/SMB) blocks
    INSIDE a read syscall, which no deadline check between files can
    preempt. On expiry the caller gets MeasurementTimeout and the daemon
    thread is abandoned — runner commands are one-shot processes, so the
    abandoned thread dies with process exit (a documented residual, same
    honesty class as the Popen->assign window in delegate.py)."""
    box = {}

    def _run():
        try:
            box["result"] = fn()
        except BaseException as e:  # propagate, including SystemExit
            box["error"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(budget_s)
    if t.is_alive():
        raise MeasurementTimeout(what)
    if "error" in box:
        raise box["error"]
    return box.get("result")
```

(c) `cmd_init` — hoist the two measurements out of the state dict literal (`:840-841`) and bound them. Replace:

```python
        "init_config_surface": config_surface(ws),
        "init_tree_sig": tree_signature(ws),
```
with entries `"init_config_surface": init_surface,` / `"init_tree_sig": init_sig,`, and insert immediately BEFORE the `state = {` literal:

```python
    try:
        init_surface = _measure_with_deadline(
            lambda: config_surface(ws), MEASUREMENT_BUDGET_S, "config_surface")
        init_sig = _measure_with_deadline(
            lambda: tree_signature(ws), MEASUREMENT_BUDGET_S, "tree_signature")
    except MeasurementTimeout as e:
        raise SystemExit(_emit({
            "error": "measurement_timeout",
            "detail": f"{e.what} exceeded the measurement budget "
                      f"({MEASUREMENT_BUDGET_S}s); refusing to baseline an "
                      f"unmeasured tree (#109: acceptance paths fail closed)",
        }, 1))
```

(d) `cmd_verify` — wrap the body so any of its four `tree_signature(ws)` / one `config_surface(ws)` calls (`:1064,:1069,:1145,:1167,:1194`) fails closed. Rename the existing function `def cmd_verify(args):` to `def _cmd_verify_impl(args):` (single-line change at the def; the body is untouched), and insert above it:

```python
def cmd_verify(args):
    """#109: any workspace measurement inside verify is budgeted; on expiry
    the refusal is a red receipt, exactly like verifier_timeout — accept
    then refuses on 'receipt not green' until a verify completes."""
    try:
        return _cmd_verify_impl(args)
    except MeasurementTimeout as e:
        ws = str(Path(args.workspace).resolve())
        task = load_task(args.task)
        sroot = _state_root(ws, args.state_dir)
        receipt = {
            "task_id": task["task_id"], "passed": False,
            "rejected": "measurement_timeout",
            "detail": f"{e.what} exceeded the measurement budget "
                      f"({MEASUREMENT_BUDGET_S}s); refusing to issue a "
                      f"receipt over an unmeasured tree (#109)",
            "dispatch_seq": len(_load_state(sroot).get("dispatches", [])),
            "verifier_argv": task["verifier"]["argv"],
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _write_json_atomic(_receipt_path(sroot, task["task_id"]), receipt)
        return _emit(receipt, 1)
```

Then, inside `_cmd_verify_impl`, replace each bare measurement call with its bounded form:
- `:1064` `pre_tree_sig = tree_signature(ws)` → `pre_tree_sig = _measure_with_deadline(lambda: tree_signature(ws), MEASUREMENT_BUDGET_S, "tree_signature")`
- `:1069` `now_surface = config_surface(ws)` → `now_surface = _measure_with_deadline(lambda: config_surface(ws), MEASUREMENT_BUDGET_S, "config_surface")`
- `:1145` and `:1167` `"tree_sig": tree_signature(ws),` → `"tree_sig": _measure_with_deadline(lambda: tree_signature(ws), MEASUREMENT_BUDGET_S, "tree_signature"),` (two occurrences — both inside receipt dicts)
- `:1194` same replacement (third occurrence).

(e) `cmd_accept` — bound the staleness measurement (`:1287`). Replace:

```python
    current = tree_signature(ws)
```
with:

```python
    try:
        current = _measure_with_deadline(
            lambda: tree_signature(ws), MEASUREMENT_BUDGET_S, "tree_signature")
    except MeasurementTimeout as e:
        raise SystemExit(_emit({
            "accepted": False,
            "reason": f"measurement_timeout: {e.what} exceeded the "
                      f"measurement budget ({MEASUREMENT_BUDGET_S}s); "
                      f"refusing rather than certifying an unmeasured tree "
                      f"(#109: acceptance paths fail closed)",
        }, 1))
```

4. Run, expect pass:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```
Expect: the six new tests pass (fast — each wedge expires in 0.2s); all existing runner suites green, including `test_tree_signature.py`, `test_acceptance_gate.py`, `test_verifier_integrity.py` (the verify refactor is behavior-preserving under the 120s default budget).

5. Commit:

```
cd mp_wt && git add harnesses/kimi-code/runner/runner.py harnesses/kimi-code/runner/tests/test_measurement_isolation.py && git commit -m "feat(runner): budget all workspace tree measurements; acceptance paths fail closed, monitors abstain (#109)"
```

---

## Task 6 — Doctrine propagation: pilot deadline, install list, docs

**Files:**
- Modify: `harnesses/kimi-code/runner/pilot.py`
- Modify: `scripts/install.py`
- Modify: `harnesses/kimi-code/skill/model-proctor/SKILL.md`
- Modify: `AGENTS.md`
- Test: `harnesses/kimi-code/runner/tests/test_measurement_isolation.py` (append class)

**Interfaces:**

```
pilot.find_wires(session_ids, not_before, homes=(), deadline=None)
    # deadline: time.monotonic() deadline; past it, return the partial list
    # (abstain with what was measured — callers already treat wires as
    # best-effort; wire_coverage returns None when unavailable, pilot.py:100+).
    # Caller at pilot.py:371 passes deadline=time.monotonic() + 30.

scripts/install.py:
    DELEGATE_FILES gains "heartbeat.py"          (:34)
    REQUIRED_AFTER_INSTALL gains "heartbeat.py"  (:45)
```

**Steps:**

1. Append the failing test to `test_measurement_isolation.py` (before the `__main__` guard):

```python
import pilot  # noqa: E402  (same sys.path entry as runner)


class FindWiresDeadlineTest(unittest.TestCase):
    """#109: session-dir statting abstains with partial results past its
    deadline instead of wedging the pilot on a sync-deferred mount."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-wires-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        home = Path(self.tmp) / "home"
        self.wire = (home / "sessions" / "s" / "sid-1" / "agents" / "a"
                     / "wire.jsonl")
        self.wire.parent.mkdir(parents=True)
        self.wire.write_text("{}\n", encoding="utf-8")
        self.home = str(home)

    def test_expired_deadline_abstains_partial(self):
        self.assertEqual(
            pilot.find_wires(["sid-1"], 0, homes=[self.home],
                             deadline=time.monotonic() - 1), [])

    def test_no_deadline_finds_wire(self):
        found = pilot.find_wires(["sid-1"], 0, homes=[self.home])
        self.assertEqual([Path(p) for p in found], [self.wire])
```

2. Run, expect failure:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v -k wires
```
Expect: `TypeError: find_wires() got an unexpected keyword argument 'deadline'`.

3. Implement.

(a) `harnesses/kimi-code/runner/pilot.py` — `find_wires` (`:76-97`) becomes:

```python
def find_wires(session_ids, not_before, homes=(), deadline=None):
    """Locate wire.jsonl files for the given child session ids.

    With TOOL-013 isolation (delegate.py injects a seeded per-dispatch
    KIMI_CODE_HOME), wires live under <child_home>/sessions/; the env/default
    home is only a fallback for isolation-disabled runs.

    #109: session dirs can sit on sync-deferred mounts. Past `deadline`
    (time.monotonic() clock) return the partial list — abstaining with what
    was measured beats wedging the pilot; callers already treat wires as
    best-effort evidence.
    """
    roots = [Path(h) / "sessions" for h in homes if h] + [_sessions_root()]
    wires = []
    for sid in session_ids:
        if not sid:
            continue
        for root in roots:
            if deadline is not None and time.monotonic() > deadline:
                return sorted(set(wires))
            if not root.is_dir():
                continue
            for p in root.glob(f"*/{sid}/agents/*/wire.jsonl"):
                try:
                    if p.stat().st_mtime >= not_before - 5:
                        wires.append(str(p))
                except OSError:
                    continue
    return sorted(set(wires))
```

(b) The call site (`pilot.py:371`) becomes:

```python
    wires = find_wires(session_ids, t0, homes=child_homes,
                       deadline=time.monotonic() + 30)
```

(c) `scripts/install.py` — `DELEGATE_FILES` (`:34`) becomes:

```python
DELEGATE_FILES = ["delegate.py", "catalog.py", "agents.example.json", "README.md",
                  "heartbeat.py"]
```

and `REQUIRED_AFTER_INSTALL` (`:45`) becomes:

```python
REQUIRED_AFTER_INSTALL = ["runner.py", "delegate.py", "catalog.py", "task_schema.py", "pricing.yaml",
                          "heartbeat.py"]
```

(`heartbeat.py` ships in the flat install beside `runner.py`, which is exactly the first candidate in `runner._heartbeat_module`; the repo layout uses the sibling-dir candidate. The CI flat-install smoke at `.github/workflows/ci.yml:42-47` verifies presence.)

(d) `harnesses/kimi-code/skill/model-proctor/SKILL.md` — in the "Resuming after a gap" section (the bullet list after the mandated `status` command, currently `:156-169`), append one bullet after the `orphaned_dispatch_ids` bullet:

```markdown
- `payload_progress` (#105) shows each open dispatch's last payload-emitted
  stage/counters. `status: "stale"` means the payload went quiet beyond the
  staleness threshold — a signal to investigate, never proof of death; kill
  decisions remain wall-clock/budget decisions, not heartbeat decisions.
  `status: "absent"` only means the payload never emitted (most don't).
  `measurement_degraded: true` with `degraded_components` (#109) means the
  monitor itself could not take a measurement (corrupt/unreadable heartbeat,
  missing protocol module) — the report is partial, not the run.
```

(e) `AGENTS.md` — in the `harnesses/kimi-code/runner/` bullet (`:34-53`), after the sentence "`init` refuses to re-baseline an initialized workspace without `--reinit`.", insert:

```markdown
Payloads may emit progress heartbeats (#105/TOOL-034): the runner forwards
`--heartbeat-file <state>/heartbeats/<dispatch_id>.jsonl` and `--dispatch-id`
to the delegate, which injects `DELEGATE_HEARTBEAT_PATH`/`DELEGATE_DISPATCH_ID`
into the child environment; the drain lands `dispatch_progress` journal
records, and `status` reports per-dispatch `payload_progress`. Observer
isolation (#109/TOOL-038): monitor paths read only runner-state and
payload-emitted files and abstain + report `measurement_degraded` on any
measurement failure (never kill, never refuse); acceptance paths
(init/verify/accept) bound every workspace-tree measurement to
`MEASUREMENT_BUDGET_S` (120s) and fail closed on expiry.
```

4. Run, expect pass — full affected suites plus the installer tests:

```
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v
cd mp_wt && python -m unittest discover -s scripts/tests -v
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
```
Expect: all green, including `scripts/tests/test_install.py` with the new REQUIRED_AFTER_INSTALL entry (the file exists in the repo, so presence checks pass).

5. Commit:

```
cd mp_wt && git add harnesses/kimi-code/runner/pilot.py scripts/install.py harnesses/kimi-code/skill/model-proctor/SKILL.md AGENTS.md harnesses/kimi-code/runner/tests/test_measurement_isolation.py && git commit -m "feat(runner): deadline-bound wire discovery; ship heartbeat.py in installs; document heartbeat + measurement doctrine (#105 #109)"
```

---

## Self-review pass

**Spec coverage:**
- mp#105 asks for: local append-only heartbeat protocol with schema (ts, stage, sub_stage, progress counters, pid) → Task 1 (`REQUIRED_KEYS` + record dict); cadence → `NOMINAL_CADENCE_S` doc contract + `STALE_AFTER_S` reader threshold; rotation → `_rotate` + `test_rotation_keeps_file_bounded_and_parseable`; emission API for payloads → `emit()` + `DELEGATE_HEARTBEAT_PATH` env contract (Tasks 1-2); watcher-side consumption contract → `_drain_payload_progress` → `dispatch_progress` journal records (Task 3) + `_payload_progress` → status (Task 4). The one-heartbeat-source defect from inventory §4 ("a live-but-wedged worker and a progressing worker produce identical journals") is closed: journals now carry payload-authored stage/counters when the payload speaks.
- mp#109 asks for: monitors read ONLY local payload-emitted/runner state → `test_status_never_measures_the_tree` pins it; measurement failure = abstain + `measurement_degraded` report, never kill → Tasks 4 (status) and 3 (drain) implement, and no code path from heartbeat data to any kill exists (grep-check at review: `payload_` and `heartbeat` must not appear in `kill_process_tree`, the delegate wait loop, or the runner's `proc.kill()` blocks); the concrete wedge point (`tree_signature`/`_walk_files` hashing with no timeout) → Task 5's daemon-thread budget; mixed doctrine (`_git_toplevel` abstains vs `_porcelain_entries` fails closed) → unified and documented in the Task 5(b) doctrine block, with each site's semantics named.

**Placeholder scan:** every task contains its real test code, real implementation code, real commands, and real commit messages; no TBD/TODO/"appropriate handling" anywhere. The only deliberate in-plan forward references are line numbers, each surveyed against `mp_wt @ 8982e24` on 2026-09-25 — if a later commit shifts them, the surrounding quoted code (not the number) is the authority.

**Type consistency across tasks:**
- `emit()` returns `dict | None`; `read_latest()` returns `tuple[dict | None, str]` with the status vocabulary `{"ok","absent","empty","corrupt","unreadable"}`; Task 3's drain consumes exactly that tuple and adds `"unavailable"` (module missing) / `"unreadable"` (exception escape) — Task 4's `_payload_progress` uses the same vocabulary plus `"stale"` (derived via `is_stale`, which returns `bool | None` and whose `None` maps to degraded, not stale, in both consumers).
- `dispatch_progress` journal records carry `payload_seq`/`payload_pid` (renamed to avoid collision with the runner's own fields) while status surfaces `pid`/`stage`/`sub_stage`/`counters` — the renaming is deliberate and consistent in Tasks 3 and 4.
- `runner.run_delegate`'s new kwargs default to `None`, so `pilot.py`'s indirect use and any other caller keep working; `fake_worker.py`'s new argparse args default to `None`, preserving its existing contract for cascade-independent runner tests.
- The two-layout resolver (`here/"heartbeat.py"` flat, `here.parent/"delegate"/"heartbeat.py"` repo) matches `scripts/install.py`'s flat copy set exactly, so Task 6(c) is what makes the flat install's resolver candidate real.

**Review-focus pinning:** (1) torn tail → Task 1 `test_torn_tail_is_skipped` + Task 4 corrupt test; (2) rotation/oversize → Task 1 rotation + bounded-tail tests; (3) silent payload → Task 3 absent test + Task 4 stale/absent tests (asserting `measurement_degraded` stays False); (4) wedged fs mid-syscall → Task 5 deadline tests (daemon-thread abandonment, 0.2s budgets, no 30s hangs in CI); (5) clock/seq anomalies → Task 1 `test_is_stale` + Task 3 drain dedupe on `epoch`. All five classes have owning tests; none is left to "manual verification".

**Out of scope (handoff notes for the issues):** condition-based kill predicates (mp#106) consume `dispatch_progress` recency but are not built here; delegate-emitted proxy heartbeats (stdout-activity-derived) are a possible later enhancement, deliberately excluded so all emitted data is payload-authored; the multi-writer rotation race is documented in the module docstring (single-emitter assumption) rather than solved.
