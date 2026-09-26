# Single Kill Authority Implementation Plan

**Planning status resolution:** this plan IS the planning that mp#107 / TOOL-036 required;
executing it closes the ticket's planning phase, and the final task's evidence satisfies the
ticket's `## Final evidence and handoff` requirement.

Executors: run this plan with the **subagent-driven-development** skill — one subagent per
task, in order (tasks 2→3→4 and 5 are sequential; 6 and 7 are independent of each other but
must both land before task 8's full-suite run).

## Goal

Exactly one component may terminate a delegate-owned worker tree: the delegate that owns the
tree's Job Object and process handles. The runner (and every other external actor) becomes
read-only with respect to payloads it does not own — its terminal action is a termination
**request** plus a **report**, never a kill. Every payload death is attributed to exactly one
authority in the run record (delegate envelope `kill_authority` key, runner journal
`dispatch_finished.kill_authority`, codex/evals result rows). The repo-wide kill-path
inventory ships as executable data (`KILL_SITES`) plus a human code table
(`docs/kill-authority.md`), enforced by an audit test that fails on drift in either direction.

## Architecture

A new stdlib-only module `harnesses/kimi-code/delegate/killauthority.py` hosts (a) the
`KILL_SITES` registry — every kill site repo-wide as data, classified — and (b) class
`KillAuthority`, which takes exclusive ownership of one dispatch's pid, job handle, and
process handle via dependency injection and is the only code that runs the five-step kill
sequence (graceful taskkill → grace → force taskkill → job close → belt-and-braces). The
delegate rewires all four exit paths through one `KillAuthority` instance and stamps the
attribution into the envelope; it also gains a `--terminate-request-file` control channel so
the runner can ask for termination instead of reaching into delegate custody with
`proc.kill()`. The runner writes the request file at its wrapper deadline, waits a bounded
`TERMINATION_REQUEST_WAIT_S`, and either relays the delegate's attributed envelope or reports
`unresolved_reported` — it never kills. Codex and evals keep killing only the direct children
they themselves spawned (no Job Object custody), now with explicit attribution.

## Tech Stack

Python 3.10, standard library only (control-plane constraint, `AGENTS.md:115-116`). Tests:
stdlib `unittest`, one discovery dir per suite, CI on `windows-latest` only
(`.github/workflows/ci.yml:25-41`). Windows Job Objects via the existing ctypes block in
`harnesses/kimi-code/delegate/delegate.py:93-227` (primitives stay there; the authority
receives them as injected callables, so `killauthority.py` itself is import-clean off-Windows).

## Spec

- Ticket: **dachent/model_proctor issue #107 / TOOL-036** — Single kill authority.
- Inventory (read first): `mp_inventory_103-110.md` at the session root, §2 (kill paths) and
  the per-issue note `### #107 / TOOL-036`. This plan's author re-surveyed every file:line the
  inventory cites; the citations below were verified against `mp_wt @ 8982e24`.

### Survey corrections to the inventory (plan wins over inventory)

1. **`_journal_open` masks `dispatch_open` on any later record sharing `dispatch_id`**
   (`runner.py:579-590`): `last[did] = e` fires for *every* event carrying `dispatch_id`,
   including `dispatch_heartbeat` (`runner.py:993-996`). While a heartbeat is the newest record
   for a dispatch, that dispatch silently disappears from `open_journal_ids` until
   `dispatch_finished` lands — the accept-time in-flight gate (`runner.py:1236-1252`) is blind
   in that window. **Not fixed in this plan** (flagged as a follow-up issue), but the new
   journal event added here uses the key `for_dispatch_id` (the existing
   `dispatch_orphaned`/`orphaned_dispatch_id` idiom, `runner.py:916`) so it does not add to
   the defect. The inventory did not catch this.
2. **Latent `UnboundLocalError` on the SIGINT interrupt path**: `_run_delegate_inner` assigns
   `_interrupt_condition` at `delegate.py:1268` without a `global` declaration, so the read at
   `delegate.py:1300` is function-local; on the SIGINT/SIGTERM path (handler sets only the
   module global) that read raises `UnboundLocalError`, which `run_delegate`'s catch-all
   converts to an `internal_error` envelope — the `interrupted` path is broken for signals
   today. **Fixed in Task 3** (required anyway for the Task 4 request-file path, which shares
   that read).
3. **`runner.py:763-774` contains a duplicated dead block**: a second `if not line:` check and
   a JSON-decode failure branch referencing an undefined name `r` (`r.stderr`, `r.stdout`) —
   unreachable NameError bait. Removed as part of the Task 5 rework of `run_delegate`.
4. Inventory says the nested-dispatch guard is `delegate.py:993-999`; the refusal block
   actually spans `:993-1004` (two `_make_result("invalid", ...)` returns).
5. Inventory/AGENTS layout implies a root `docs/` exists; it does not. Task 8 creates it
   (`AGENTS.md:11` already lists `docs/` as a root asset class, so this is consistent).
6. `scripts/install.py:34` (`DELEGATE_FILES`) and `:45` (`REQUIRED_AFTER_INSTALL`) are explicit
   file lists: a new `killauthority.py` sibling must be added to both, or the flat install at
   `C:\Tools\model-proctor\` produces a delegate that fails on `import killauthority` — and the
   CI flat-install smoke (`ci.yml:42-47`) will not catch it unless `REQUIRED_AFTER_INSTALL` is
   updated. The inventory does not mention this coupling.

## Global Constraints

- **Stdlib-only Python 3.10** for everything in this plan (`AGENTS.md:115-116`).
- **Envelope status vocabulary is frozen**: `completed | failed | timeout | interrupted |
  internal_error | invalid` (`delegate.py:49-53`, `_make_result` `delegate.py:1403-1427`;
  runner synthetic envelopes `runner.py:740,756,765`; frozen cascade's
  `TERMINAL_ENVELOPE_STATUSES` `cascade.py:121`). This plan adds exactly one *key*
  (`kill_authority`), never a new status. Runner-requested kills surface as status
  `interrupted` (exit 130) — note the frozen cascade treats `interrupted` as non-terminal
  (`cascade.py:121`); acceptable because cascade is a frozen artifact, not the live path.
- **`cascade.py` is a frozen research artifact** (`AGENTS.md:24-30`): registered in
  `KILL_SITES` as `frozen_artifact`, never modified.
- **New journal records must not carry a bare `dispatch_id` key** (masking hazard, correction
  #1). Use `for_dispatch_id`.
- **No git mutations without explicit user confirmation** (`AGENTS.md:120`). Each task ends
  with the exact commit command; the executor stages them, the user authorizes execution.
  Commit style follows the repo log (`fix(codex): ...`, `feat(codex): ...`).
- **Test commands** (per `AGENTS.md:69-83`): delegate suite
  `python -m unittest discover -s harnesses/kimi-code/delegate/tests -v`; runner suite
  `python -m unittest discover -s harnesses/kimi-code/runner/tests -v`; codex suite
  `python -m unittest discover -s harnesses/codex/delegate/tests -v`; evals suite
  `python -m unittest discover -s evals/tests -v`. Single-file focus:
  add `-p <file>.py` to the discover command.
- **Work branch**: `mp_wt` sits on `tool-032-detached-custody` (== origin/main @ 8982e24).
  Create `tool-036-single-kill-authority` from it before Task 1 (branch creation is also a git
  mutation — get the user's confirmation together with the first commit).

## Review Focus

Five input classes / failure modes the spec implies that no single task's happy-path tests
exercise. Each is pinned to the task that owns its test or its documented residual:

1. **Delegate wedged *inside* its own kill sequence** (taskkill hangs are swallowed at 10s
   each, so worst case ≈ grace + 55s before the envelope). If that exceeds the runner's
   `TERMINATION_REQUEST_WAIT_S`, the journal says `unresolved` even though the authority later
   executed — the record must not claim the runner killed anything. *Pinned to Task 5*: the
   ignore-mode test asserts the wedged delegate is **still alive** after the runner reports;
   the partially-executing wedge is a documented residual in `docs/kill-authority.md` (Task 8),
   not hermetically testable without a genuinely wedged delegate.
2. **Stale or racing terminate-request file.** The delegate consumes the file the moment it
   appears; a file left by a previous dispatch at a reused path would insta-kill a healthy
   worker. Defense: per-dispatch uuid path (`{dispatch_id}.terminate` — dispatch_id is
   `uuid4`, `runner.py:923`) + unlink-before-spawn. *Pinned to Task 4* (stale-file semantics
   test — documents that the delegate does not defend itself) *and Task 5* (sidecar assertion
   that the file did not exist when the delegate started).
3. **Request-channel write failure** (wedged OneDrive/SMB state dir — the #109 class): the
   runner's request write must be best-effort, never fatal, and journaled as
   `request_write_failed`. *Pinned to Task 5* (`_request_termination` unit test with an
   unwritable path).
4. **Job-Object creation failure** (`job_warning=True`, `delegate.py:1198-1218`): the
   authority must still run the taskkill steps and attribute exactly, with `job=None`.
   *Pinned to Task 2* (unit test) and *Task 3* (existing
   `test_job_warning_false_on_happy_path` `test_delegate.py:1150` must stay green).
5. **`allow_breakaway` payloads**: attribution records what the authority *did*, never a
   guarantee that breakaway descendants died (documented residual `delegate.py:95-107` —
   a breakaway orphan surviving a `delegate:timeout` attribution is the feature working).
   *Pinned to Task 2* (docstring language) and *Task 3* (kernel-readback suite
   `test_breakaway.py:59-101` must stay green).

---

## Task 1 — Kill-site registry + audit test (inventory as executable data)

### Files

- Create: `harnesses/kimi-code/delegate/killauthority.py`
- Test: `harnesses/kimi-code/delegate/tests/test_kill_authority.py` (new)

### Interfaces

- Produces: `killauthority.ATTRIBUTIONS: tuple[str, ...]`,
  `killauthority.KILL_SITES: list[dict]` with per-entry keys
  `site, file, classification, expected, must_contain, must_not_contain, note`
  (`expected`/`must_*` optional; `expected=None` exempts authority-class files from counts).
- Consumes: nothing (module is self-contained; audit test reads it).
- Forbidden-pattern names the audit enforces (regexes live in the test):
  `proc_kill`=`\.kill\(\s*\)`, `proc_terminate`=`\.terminate\(\s*\)`, `taskkill`=`\btaskkill\b`,
  `terminate_process`=`\bTerminateProcess\b`, `os_kill`=`\bos\.kill\(`.
- Surveyed counts @8982e24 (verified by grep this planning pass): runner.py `proc_kill`=2,
  codex delegate.py `proc_kill`=1, evals/run_eval.py `taskkill`=1, all others 0. If the audit's
  first run disagrees, **stop — the tree drifted; re-survey before continuing.**

### Steps

1. Write the failing test `harnesses/kimi-code/delegate/tests/test_kill_authority.py`:

```python
#!/usr/bin/env python3
"""Kill-site audit (TOOL-036, issue #107): the registry is the law.

Every kill-primitive occurrence in scoped production files must match
killauthority.KILL_SITES exactly; source drift in either direction fails the
suite. Authority-class files (expected=None) are exempt from counts and are
guarded by literal markers instead.

Run: python -m unittest discover -s delegate/tests -v
"""

import re
import sys
import unittest
from pathlib import Path

_DELEGATE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DELEGATE_DIR))

import killauthority  # noqa: E402

REPO_ROOT = _DELEGATE_DIR.parents[2]

FORBIDDEN = {
    "proc_kill": r"\.kill\(\s*\)",
    "proc_terminate": r"\.terminate\(\s*\)",
    "taskkill": r"\btaskkill\b",
    "terminate_process": r"\bTerminateProcess\b",
    "os_kill": r"\bos\.kill\(",
}

VALID_CLASSIFICATIONS = {
    "authority", "custody_violation_documented", "external_readonly",
    "own_child_cleanup", "stdlib_implicit", "frozen_artifact", "policy_pure",
}


class TestKillSiteRegistry(unittest.TestCase):
    def test_registry_schema(self):
        for entry in killauthority.KILL_SITES:
            with self.subTest(site=entry["site"]):
                self.assertIn(entry["classification"], VALID_CLASSIFICATIONS)
                self.assertTrue(entry["file"])
                self.assertTrue(entry["note"])

    def test_registry_counts_match_source(self):
        for entry in killauthority.KILL_SITES:
            with self.subTest(site=entry["site"]):
                path = REPO_ROOT / entry["file"]
                self.assertTrue(path.is_file(), f"missing file: {entry['file']}")
                text = path.read_text(encoding="utf-8")
                for marker in entry.get("must_contain", []):
                    self.assertIn(marker, text,
                                  f"{entry['file']} lost marker {marker!r}")
                for marker in entry.get("must_not_contain", []):
                    self.assertNotIn(marker, text,
                                     f"{entry['file']} gained marker {marker!r}")
                expected = entry.get("expected")
                if expected is None:
                    continue  # authority-class: marker-guarded, not counted
                for name, pattern in FORBIDDEN.items():
                    actual = len(re.findall(pattern, text))
                    self.assertEqual(
                        actual, expected.get(name, 0),
                        f"{entry['file']}:{name} expected "
                        f"{expected.get(name, 0)}, found {actual} — re-survey "
                        f"and justify in KILL_SITES")


if __name__ == "__main__":
    unittest.main()
```

2. Run it, expect fail (`ModuleNotFoundError: No module named 'killauthority'`):

```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_kill_authority.py -v
```

3. Create `harnesses/kimi-code/delegate/killauthority.py` (registry mirrors *current* reality;
   the runner entry is honestly labeled a documented violation until Task 5 removes it):

```python
#!/usr/bin/env python3
"""Kill-path registry and single kill authority (TOOL-036, issue #107).

One component terminates a delegate-owned worker tree: the delegate that owns
its Job Object and process handles. External actors (runner, cascade, evals,
codex adapter) are read-only with respect to payloads they do not own: their
terminal action is a termination REQUEST plus a report, never a kill. Every
payload death is attributed to exactly one authority via the envelope/journal
key ``kill_authority``.

``KILL_SITES`` is the repo-wide kill-path inventory as data; the audit test
(harnesses/kimi-code/delegate/tests/test_kill_authority.py) fails the suite
when source drifts from it in either direction.

Python 3.10, standard library only.
"""

#: Attribution vocabulary for the `kill_authority` envelope/journal key.
#: "none" means the payload root exited on its own — no kill occurred.
#: The vocabulary describes what the authority DID; for allow_breakaway
#: agents it is not a guarantee that detached descendants died (see the
#: residual comment at delegate.py:95-107).
ATTRIBUTIONS = (
    "none",
    "delegate:timeout",
    "delegate:interrupted",
    "delegate:runner_requested",
    "unresolved_reported",       # runner requested; delegate never confirmed
    "codex:cleanup_kill",        # codex adapter reaping its own direct child
    "eval:taskkill_tree_force",  # evals harness reaping its own kimi.exe tree
)

#: Repo-wide kill-path inventory. Entry fields:
#:   site            human name
#:   file            repo-relative path, forward slashes
#:   classification  authority | custody_violation_documented |
#:                   external_readonly | own_child_cleanup | stdlib_implicit |
#:                   frozen_artifact | policy_pure
#:   expected        forbidden-pattern -> count; None exempts authority files
#:                   (they are marker-guarded instead)
#:   must_contain / must_not_contain   literal drift tripwires
#:   note            why this site exists and who owns it
KILL_SITES = [
    {
        "site": "delegate kill authority module",
        "file": "harnesses/kimi-code/delegate/killauthority.py",
        "classification": "authority",
        "expected": None,
        "note": "The only module permitted to execute a worker-tree kill.",
    },
    {
        "site": "delegate job primitives + dispatch wiring",
        "file": "harnesses/kimi-code/delegate/delegate.py",
        "classification": "authority",
        "expected": None,
        "must_contain": ["def kill_process_tree"],
        "note": "Kill sequence still inlined as kill_process_tree "
                "(delegate.py:865-926); Task 3 funnels it into KillAuthority "
                "and flips these markers.",
    },
    {
        "site": "runner wrapper-deadline kill",
        "file": "harnesses/kimi-code/runner/runner.py",
        "classification": "custody_violation_documented",
        "expected": {"proc_kill": 2},
        "note": "proc.kill() on the delegate at the wrapper deadline "
                "(runner.py:738 and :754) reaches into delegate custody; the "
                "delegate's job close then wipes the worker tree. Task 5 "
                "removes both sites; the runner becomes request/report-only.",
    },
    {
        "site": "pilot runner spawn",
        "file": "harnesses/kimi-code/runner/pilot.py",
        "classification": "stdlib_implicit",
        "expected": {},
        "note": "subprocess.run(timeout=900) at pilot.py:69-73; the kill on "
                "TimeoutExpired is inside stdlib. Eval driver, no custody of "
                "the delegate-owned worker tree.",
    },
    {
        "site": "evals kimi.exe timeout kill",
        "file": "evals/run_eval.py",
        "classification": "own_child_cleanup",
        "expected": {"taskkill": 1},
        "note": "taskkill /T /F at run_eval.py:95-98 on a kimi.exe the eval "
                "harness spawned itself (no Job Object). Task 7 attributes it "
                "as eval:taskkill_tree_force in the result row.",
    },
    {
        "site": "evals plain-arm delegate spawn",
        "file": "evals/plain_arm.py",
        "classification": "stdlib_implicit",
        "expected": {},
        "note": "subprocess.run(timeout=timeout_s+300) at plain_arm.py:63-67; "
                "implicit stdlib kill of the direct child only.",
    },
    {
        "site": "codex adapter cleanup kill",
        "file": "harnesses/codex/delegate/delegate.py",
        "classification": "own_child_cleanup",
        "expected": {"proc_kill": 1},
        "note": "_cleanup (codex/delegate.py:96-114) kills only the adapter's "
                "own direct child after a 1s grace; no Job Object, no tree "
                "custody. Task 6 attributes it as codex:cleanup_kill.",
    },
    {
        "site": "zcode verifier/git spawns",
        "file": "harnesses/zcode/zproctor.py",
        "classification": "stdlib_implicit",
        "expected": {},
        "note": "subprocess.run timeouts at zproctor.py:41-53,56-59; implicit "
                "stdlib kills of verifier/git children only.",
    },
    {
        "site": "cascade backstop kill (frozen)",
        "file": "harnesses/kimi-code/cascade/cascade.py",
        "classification": "frozen_artifact",
        "expected": {},
        "note": "Frozen research artifact (AGENTS.md:24-30): the kill on "
                "backstop expiry (cascade.py:476-483) is inside stdlib "
                "subprocess.run. Registered for completeness; never modified.",
    },
    {
        "site": "core decision modules",
        "file": "core/decisions.py",
        "classification": "policy_pure",
        "expected": {},
        "note": "Pure functions over data (core/decisions.py:1-12); may never "
                "touch a process.",
    },
    {
        "site": "core task schema",
        "file": "core/task_schema.py",
        "classification": "policy_pure",
        "expected": {},
        "note": "Schema validation only; may never touch a process.",
    },
]
```

4. Run the audit test again, expect pass. Also run the whole delegate suite to confirm no
   collection side effects:

```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
```

5. Commit (after user authorization):

```
git add harnesses/kimi-code/delegate/killauthority.py harnesses/kimi-code/delegate/tests/test_kill_authority.py docs/superpowers/plans/2026-09-25-tool-036-single-kill-authority.md
git commit -m "feat(delegate): kill-site registry and audit test (TOOL-036 #107, task 1)"
```

---

## Task 2 — `KillAuthority` class (ownership + attribution, fully unit-tested)

### Files

- Modify: `harnesses/kimi-code/delegate/killauthority.py`
- Test: `harnesses/kimi-code/delegate/tests/test_kill_authority.py` (append)

### Interfaces

- Produces (Task 3 consumes all of these):

```python
class KillAuthority:
    """Owns one dispatch's pid, job handle, and process handle."""
    attribution: str  # "none" until terminate() runs; then "delegate:<reason>"

    def __init__(self, pid, grace_seconds, *, job=None, proc_handle=None,
                 job_close=None, proc_close=None, tool_argv0="taskkill",
                 tool_env=None, runner=subprocess.run): ...
    def terminate(self, reason): ...  # reason in TERMINATE_REASONS -> attribution str; idempotent
    def release(self): ...            # normal-completion custody close -> "none"; idempotent
```

- `killauthority.TERMINATE_REASONS = ("timeout", "interrupted", "runner_requested")`.
- DI contract: `runner` is a `subprocess.run`-shaped callable; `job_close`/`proc_close` are
  the delegate's `close_job`/`close_process_handle` primitives (or `None` off-Windows).
- Ordering contract `terminate()` must honor (mirrors `delegate.py:865-926` today): graceful
  `taskkill /PID <pid> /T` → grace poll-sleep → force `/T /F` → job close → force `/T /F`
  again → close process handle. `release()` closes process handle then job handle, nothing
  else. All tool/handle failures are swallowed (existing doctrine `delegate.py:886-887`).

### Steps

1. Append the failing tests to `test_kill_authority.py`:

```python
import subprocess


class _Recorder:
    """DI fakes recording every authority action in order."""

    def __init__(self):
        self.calls = []

    def runner(self, argv, **kwargs):
        self.calls.append(("tool", tuple(argv)))
        return subprocess.CompletedProcess(argv, 0)

    def job_close(self, job):
        self.calls.append(("job_close", job))

    def proc_close(self, handle):
        self.calls.append(("proc_close", handle))


def _make_authority(rec, grace=0, job="JOB", proc_handle="PH"):
    return killauthority.KillAuthority(
        1234, grace, job=job, proc_handle=proc_handle,
        job_close=rec.job_close, proc_close=rec.proc_close,
        tool_argv0="taskkill", tool_env=None, runner=rec.runner)


class TestKillAuthority(unittest.TestCase):
    def test_terminate_order_and_attribution(self):
        rec = _Recorder()
        authority = _make_authority(rec)
        self.assertEqual(authority.attribution, "none")
        attribution = authority.terminate("timeout")
        self.assertEqual(attribution, "delegate:timeout")
        self.assertEqual(authority.attribution, "delegate:timeout")
        self.assertEqual(rec.calls, [
            ("tool", ("taskkill", "/PID", "1234", "/T")),
            ("tool", ("taskkill", "/PID", "1234", "/T", "/F")),
            ("job_close", "JOB"),
            ("tool", ("taskkill", "/PID", "1234", "/T", "/F")),
            ("proc_close", "PH"),
        ])

    def test_terminate_without_job_still_kills_and_attributes(self):
        """job_warning=True path: no job handle, taskkill steps must still run."""
        rec = _Recorder()
        authority = _make_authority(rec, job=None)
        self.assertEqual(authority.terminate("interrupted"),
                         "delegate:interrupted")
        self.assertEqual(rec.calls, [
            ("tool", ("taskkill", "/PID", "1234", "/T")),
            ("tool", ("taskkill", "/PID", "1234", "/T", "/F")),
            ("tool", ("taskkill", "/PID", "1234", "/T", "/F")),
            ("proc_close", "PH"),
        ])

    def test_terminate_rejects_unknown_reason(self):
        rec = _Recorder()
        authority = _make_authority(rec)
        with self.assertRaises(ValueError):
            authority.terminate("annoyed")
        self.assertEqual(rec.calls, [])

    def test_terminate_is_idempotent(self):
        rec = _Recorder()
        authority = _make_authority(rec)
        authority.terminate("timeout")
        calls_after_first = list(rec.calls)
        self.assertEqual(authority.terminate("interrupted"),
                         "delegate:timeout")
        self.assertEqual(rec.calls, calls_after_first)

    def test_release_is_custody_close_only(self):
        rec = _Recorder()
        authority = _make_authority(rec)
        self.assertEqual(authority.release(), "none")
        self.assertEqual(authority.attribution, "none")
        self.assertEqual(rec.calls, [("proc_close", "PH"), ("job_close", "JOB")])
        authority.release()  # idempotent
        self.assertEqual(rec.calls, [("proc_close", "PH"), ("job_close", "JOB")])

    def test_terminate_after_release_is_a_noop(self):
        rec = _Recorder()
        authority = _make_authority(rec)
        authority.release()
        self.assertEqual(authority.terminate("timeout"), "none")
        self.assertEqual(len(rec.calls), 2)

    def test_tool_failure_is_swallowed(self):
        rec = _Recorder()

        def exploding_runner(argv, **kwargs):
            raise OSError("taskkill missing")

        authority = killauthority.KillAuthority(
            1234, 0, job="JOB", proc_handle="PH",
            job_close=rec.job_close, proc_close=rec.proc_close,
            tool_argv0="taskkill", tool_env=None, runner=exploding_runner)
        self.assertEqual(authority.terminate("timeout"), "delegate:timeout")
        self.assertIn(("job_close", "JOB"), rec.calls)
```

2. Run, expect fail (`AttributeError: ... no attribute 'KillAuthority'`):

```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_kill_authority.py -v
```

3. Append to `killauthority.py` (this is the `delegate.py:865-926` sequence, moved verbatim in
   behavior, plus ownership/idempotence/attribution):

```python
import subprocess
import time

#: Reasons terminate() accepts; the attribution is "delegate:<reason>".
TERMINATE_REASONS = ("timeout", "interrupted", "runner_requested")


class KillAuthority:
    """The single kill authority for one delegate-owned worker tree.

    Constructed by the delegate immediately after Popen/job setup; every exit
    path routes through exactly one of terminate() or release(). terminate()
    runs the full kill sequence (graceful taskkill /T -> grace wait -> force
    /T /F while parent-PID links are intact -> job close, which makes the
    kernel terminate anything still in the job -> belt-and-braces /T /F) and
    sets the attribution the delegate stamps into the envelope. release() is
    normal-completion custody: closing the job handle makes the kernel reap
    any straggler still inside it — an implicit kill owned by this authority,
    but not a payload kill (the root already exited), so the attribution
    stays "none".

    For allow_breakaway agents the attribution records what this authority
    DID, not a guarantee that detached descendants died — surviving the run
    is the feature breakaway buys (delegate.py:95-107).

    Both terminal methods are idempotent: the second call returns the
    existing attribution without repeating any action. All tool/handle
    failures are swallowed (doctrine: a kill path must never mask the run's
    real outcome with its own exception).
    """

    def __init__(self, pid, grace_seconds, *, job=None, proc_handle=None,
                 job_close=None, proc_close=None, tool_argv0="taskkill",
                 tool_env=None, runner=subprocess.run):
        self._pid = pid
        self._grace = grace_seconds
        self._job = job
        self._proc_handle = proc_handle
        self._job_close = job_close
        self._proc_close = proc_close
        self._tool = tool_argv0
        self._tool_env = tool_env
        self._runner = runner
        self._closed = False
        self.attribution = "none"

    def terminate(self, reason):
        """Run the full kill sequence; return the attribution. Idempotent."""
        if reason not in TERMINATE_REASONS:
            raise ValueError(f"unknown terminate reason: {reason!r}")
        if self._closed:
            return self.attribution
        # Step 1: graceful taskkill (no /F) — WM_CLOSE; cheap, non-destructive.
        self._taskkill(["/PID", str(self._pid), "/T"])
        # Step 2: grace wait (poll-sleep; the caller reaps after we return).
        if self._grace > 0:
            deadline = time.monotonic() + self._grace
            while time.monotonic() < deadline:
                time.sleep(min(0.2, deadline - time.monotonic()))
        # Step 3: force taskkill /T /F while parent-PID links are intact, so
        # out-of-job descendants (the Popen->assign window) are still reachable.
        self._taskkill(["/PID", str(self._pid), "/T", "/F"])
        # Step 4: job close — kernel terminates anything still in the job.
        self._close_job_once()
        # Step 5: belt-and-braces force kill after job close.
        self._taskkill(["/PID", str(self._pid), "/T", "/F"])
        self.attribution = f"delegate:{reason}"
        self._closed = True
        self._close_proc_handle_once()
        return self.attribution

    def release(self):
        """Normal-completion custody close. Returns "none". Idempotent."""
        if self._closed:
            return self.attribution
        self._closed = True
        self._close_proc_handle_once()
        self._close_job_once()
        return self.attribution

    def _taskkill(self, argv_tail):
        try:
            self._runner([self._tool] + argv_tail, capture_output=True,
                         timeout=10, shell=False, env=self._tool_env)
        except Exception:
            pass

    def _close_job_once(self):
        if self._job is not None and self._job_close is not None:
            try:
                self._job_close(self._job)
            except Exception:
                pass
            self._job = None

    def _close_proc_handle_once(self):
        if self._proc_handle is not None and self._proc_close is not None:
            try:
                self._proc_close(self._proc_handle)
            except Exception:
                pass
            self._proc_handle = None
```

4. Run, expect pass (all of `test_kill_authority.py`):

```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_kill_authority.py -v
```

5. Commit:

```
git add harnesses/kimi-code/delegate/killauthority.py harnesses/kimi-code/delegate/tests/test_kill_authority.py
git commit -m "feat(delegate): KillAuthority owns pid/job/proc handles with attribution (TOOL-036 #107, task 2)"
```

---

## Task 3 — Rewire the delegate through `KillAuthority`; envelope gains `kill_authority`

### Files

- Modify: `harnesses/kimi-code/delegate/delegate.py`
- Modify: `harnesses/kimi-code/delegate/killauthority.py` (registry marker flip)
- Modify: `harnesses/kimi-code/delegate/tests/test_delegate.py` (`_ENVELOPE_KEYS` only)
- Modify: `scripts/install.py` (`DELEGATE_FILES` :34, `REQUIRED_AFTER_INSTALL` :45)
- Test: `harnesses/kimi-code/delegate/tests/test_kill_authority.py` (append)

### Interfaces

- Consumes: `killauthority.KillAuthority`, `TERMINATE_REASONS` (Task 2).
- Produces: envelope key `kill_authority: str` on **every** delegate envelope —
  `"none"` default in `_make_result`; `delegate:timeout` / `delegate:interrupted` from the two
  kill paths; validation/launch-failure envelopes keep `"none"`.
  `_make_result(status, ..., error=None, kill_authority="none")` — new trailing keyword param.
- Deletes: `kill_process_tree` (`delegate.py:865-926`) and `_cleanup_handles`
  (`delegate.py:1386-1400`). Confirmed by repo-wide grep: the only callers of
  `kill_process_tree` are `delegate.py:1272` and `:1307`; the only callers of
  `_cleanup_handles` are `:1302,:1336,:1360,:1382`.

### Steps

1. Test-first edits. (a) In `test_delegate.py` add one line to `_ENVELOPE_KEYS`
   (`test_delegate.py:315-333`): `"kill_authority": str,`. (b) Flip the registry markers in
   `killauthority.py`'s delegate entry: replace `"must_contain": ["def kill_process_tree"]`
   with `"must_not_contain": ["kill_process_tree"]` and update the note to past tense.
   (c) Append to `test_kill_authority.py`:

```python
import os
import time

from test_delegate import DelegateTestBase, make_agent, _PID_SLEEPER  # noqa: E402


class TestKillAttributionEnvelope(DelegateTestBase):
    def test_completed_run_attributes_none(self):
        out, err, rc = self._run("test-agent", task="hello")
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertEqual(result["kill_authority"], "none")

    def test_failed_run_attributes_none(self):
        failer = self._script("failer", "import sys; sys.exit(3)\n")
        cfg = self._config({"test-agent": make_agent(failer)})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = self._assert_result(out, err, rc, "failed", 0)
        self.assertEqual(result["kill_authority"], "none")

    def test_timeout_run_attributes_delegate_timeout(self):
        sleeper = self._script("ka_sleeper", _PID_SLEEPER)
        pid_file = os.path.join(self.workspace, "ka_pid.txt")
        cfg = self._config({
            "test-agent": make_agent(
                sleeper, prompt_delivery="argument", extra_args=[pid_file],
                default_timeout=3, minimum_timeout=1, maximum_timeout=300),
        }, extra={"default_kill_grace_seconds": 1})
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        result = self._assert_result(out, err, rc, "timeout", 124)
        self.assertEqual(result["kill_authority"], "delegate:timeout")
        with open(pid_file, "r") as f:
            child_pid = int(f.read().strip())
        time.sleep(2)
        import delegate
        self.assertFalse(delegate.is_pid_alive(child_pid),
                         f"child {child_pid} alive after authority terminate")
```

2. Run, expect fail — schema assertion reports `Missing key: kill_authority`, and the audit
   fails on the flipped markers (`kill_process_tree` still present in delegate.py):

```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
```

3. Implement in `delegate.py`:

   a. After the stdlib imports, add `import killauthority` (sibling module; script-dir is
      `sys.path[0]` both in-repo and flat-installed).
   b. At the top of `_run_delegate_inner` (`delegate.py:986-987`) add
      `global _interrupt_condition, _termination_requested` and add the module global
      `_termination_requested = False` beside `_interrupted` (`delegate.py:933`). This also
      fixes the latent SIGINT-path `UnboundLocalError` (survey correction #2): the
      `except KeyboardInterrupt` assignment at `:1268` currently makes every read of
      `_interrupt_condition` in the function local.
   c. Replace the job-setup block (`:1198-1218`) with:

```python
    job = None
    proc_handle = None
    job_warning = False
    if _IS_WINDOWS:
        try:
            job = create_kill_on_close_job(bool(agent.get("allow_breakaway", False)))
            proc_handle = assign_process_to_job(job, proc.pid)
        except Exception:
            job_warning = True
    # TOOL-036: one authority owns pid + job + process handle from here on;
    # every exit path below routes through it. On partial job-setup failure it
    # still closes whatever handles exist (release() is a pure custody close).
    authority = killauthority.KillAuthority(
        proc.pid, cfg["default_kill_grace_seconds"],
        job=job, proc_handle=proc_handle,
        job_close=close_job if _IS_WINDOWS else None,
        proc_close=close_process_handle if _IS_WINDOWS else None,
        tool_argv0=_TASKKILL_EXE, tool_env=_MINIMAL_TOOL_ENV,
    )
    if job_warning:
        authority.release()
```

   d. Interrupted path (`:1270-1303`): replace `kill_process_tree(...)` / `job = None` with

```python
    if _interrupted.is_set():
        reason = "runner_requested" if _termination_requested else "interrupted"
        attribution = authority.terminate(reason)
```

      pass `kill_authority=attribution` into that path's `_make_result(...)`, and delete the
      trailing `_cleanup_handles(proc_handle, job)` line (the authority already closed both).
   e. Timeout path (`:1306-1337`): `attribution = authority.terminate("timeout")`,
      `kill_authority=attribution` in `_make_result`, delete `_cleanup_handles(...)`.
   f. Reader-error path (`:1348-1361`) and normal path (`:1363-1383`): replace
      `_cleanup_handles(proc_handle, job)` with `authority.release()`; `_make_result` keeps the
      default `kill_authority="none"`.
   g. `_make_result` (`:1403-1427`): add trailing param `kill_authority="none"` and dict entry
      `"kill_authority": kill_authority,`.
   h. Delete the `kill_process_tree` function (`:865-926`, keep the section header comment,
      pointing at killauthority) and `_cleanup_handles` (`:1386-1400`). Update the two comments
      that name `kill_process_tree`: the breakaway design comment at `:100` ("because
      kill_process_tree force-taskkill /T /F's them" → "because KillAuthority.terminate
      force-taskkills /T /F them before closing the job").
   i. `scripts/install.py`: `DELEGATE_FILES = ["delegate.py", "killauthority.py",
      "catalog.py", "agents.example.json", "README.md"]` and
      `REQUIRED_AFTER_INSTALL = ["runner.py", "delegate.py", "killauthority.py",
      "catalog.py", "task_schema.py", "pricing.yaml"]`.

4. Run, expect pass — full delegate suite, and the breakaway kernel-readback suite must stay
   green (Review Focus #5):

```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
python -c "import sys; sys.path.insert(0, 'scripts'); import install; missing = [n for n in install.REQUIRED_AFTER_INSTALL if not (install.KIMI / 'delegate' / n).is_file() and not (install.KIMI / 'runner' / n).is_file() and not (install.ROOT / 'core' / n).is_file() and not (install.ROOT / 'evals' / n).is_file()]; print('missing:', missing); sys.exit(1 if missing else 0)"
```

   (The second command is the CI flat-install smoke from `ci.yml:42-47`, run locally.)

5. Commit:

```
git add harnesses/kimi-code/delegate/delegate.py harnesses/kimi-code/delegate/killauthority.py harnesses/kimi-code/delegate/tests/test_delegate.py harnesses/kimi-code/delegate/tests/test_kill_authority.py scripts/install.py
git commit -m "feat(delegate): route every exit path through KillAuthority, stamp kill_authority in envelope (TOOL-036 #107, task 3)"
```

---

## Task 4 — `--terminate-request-file` control channel (delegate side)

### Files

- Modify: `harnesses/kimi-code/delegate/delegate.py`
- Modify: `harnesses/kimi-code/delegate/tests/test_delegate.py` (test helper only)
- Test: `harnesses/kimi-code/delegate/tests/test_kill_authority.py` (append)

### Interfaces

- Produces: CLI arg `--terminate-request-file PATH` (optional, default `None`). When the file
  appears, the wait loop treats it as an external termination request: sets
  `_termination_requested = True`, `_interrupted`, and
  `_interrupt_condition = "termination requested by external actor via
  --terminate-request-file"`, then flows through the existing interrupted path — status
  `interrupted`, exit 130, `kill_authority: "delegate:runner_requested"` (Task 3 wiring).
- Consumes: Task 3's `reason` branch in the interrupted path.
- Helper change: `tests/test_delegate.py::run_delegate(..., terminate_request_file=None)`
  (`:279-297`) appends `argv += ["--terminate-request-file", terminate_request_file]` when
  set; `DelegateTestBase._run` (`:378-385`) passes it through.

### Steps

1. Failing tests — append to `test_kill_authority.py`:

```python
import json
import threading


class TestTerminateRequestFile(DelegateTestBase):
    def test_request_file_interrupts_with_runner_attribution(self):
        sleeper = self._script("tr_sleeper", _PID_SLEEPER)
        pid_file = os.path.join(self.workspace, "tr_pid.txt")
        req_file = os.path.join(self.tmpdir, "terminate.request")
        cfg = self._config({
            "test-agent": make_agent(
                sleeper, prompt_delivery="argument", extra_args=[pid_file],
                default_timeout=60, minimum_timeout=1, maximum_timeout=300),
        }, extra={"default_kill_grace_seconds": 1})

        def requester():
            time.sleep(2)
            with open(req_file, "w", encoding="utf-8") as f:
                json.dump({"reason": "delegate_wrapper_timeout"}, f)

        t = threading.Thread(target=requester, daemon=True)
        t.start()
        t0 = time.monotonic()
        out, err, rc = self._run("test-agent", task="ignored", config=cfg,
                                 terminate_request_file=req_file)
        wall = time.monotonic() - t0
        result = self._assert_result(out, err, rc, "interrupted", 130)
        self.assertEqual(result["kill_authority"], "delegate:runner_requested")
        self.assertIn("terminate-request-file", result["error"])
        self.assertLess(wall, 30)  # well under the 60s child timeout
        with open(pid_file, "r") as f:
            child_pid = int(f.read().strip())
        time.sleep(2)
        import delegate
        self.assertFalse(delegate.is_pid_alive(child_pid),
                         f"child {child_pid} alive after requested terminate")

    def test_stale_request_file_kills_immediately(self):
        """The delegate does NOT defend against a stale file: a file present at
        launch is consumed on the first poll. Stale-file defense is the
        runner's job (per-dispatch uuid path + unlink-before-spawn, Task 5)."""
        req_file = os.path.join(self.tmpdir, "stale.request")
        with open(req_file, "w", encoding="utf-8") as f:
            f.write("{}")
        out, err, rc = self._run("test-agent", task="hello",
                                 terminate_request_file=req_file)
        result = self._assert_result(out, err, rc, "interrupted", 130)
        self.assertEqual(result["kill_authority"], "delegate:runner_requested")
```

2. Run, expect fail (`TypeError: _run() got an unexpected keyword argument
   'terminate_request_file'` — then, after the helper edit, `status invalid / exit 64`
   because the delegate rejects the unknown arg):

```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_kill_authority.py -v
```

3. Implement:
   a. `tests/test_delegate.py`: add `terminate_request_file=None` to `run_delegate` (`:279`)
      and to `DelegateTestBase._run` (`:378`), appending
      `argv += ["--terminate-request-file", terminate_request_file]` when set (place beside
      the existing `--resume-from` append at `:290`).
   b. `delegate.py` CLI (`:1477` area, after `--timeout`):

```python
    parser.add_argument("--terminate-request-file", default=None,
                        help="TOOL-036: path an external actor (the runner) "
                             "creates to request termination. The delegate "
                             "remains the sole kill authority and executes "
                             "the kill itself.")
```

   c. Wait loop (`:1249-1265`): add the poll between the `_interrupted` check and
      `proc.poll()`:

```python
            if term_file and os.path.exists(term_file):
                _termination_requested = True
                _interrupted.set()
                _interrupt_condition = ("termination requested by external "
                                        "actor via --terminate-request-file")
                break
```

      with `term_file = getattr(args, "terminate_request_file", None)` captured just before
      the loop. (`_termination_requested` / `_interrupt_condition` writes are covered by the
      `global` declaration added in Task 3.)

4. Run, expect pass — the two new tests plus the full delegate suite:

```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
```

5. Commit:

```
git add harnesses/kimi-code/delegate/delegate.py harnesses/kimi-code/delegate/tests/test_delegate.py harnesses/kimi-code/delegate/tests/test_kill_authority.py
git commit -m "feat(delegate): --terminate-request-file control channel (TOOL-036 #107, task 4)"
```

---

## Task 5 — Runner becomes read-only: request → bounded wait → report, never kill

### Files

- Modify: `harnesses/kimi-code/runner/runner.py`
- Modify: `harnesses/kimi-code/delegate/killauthority.py` (registry flip: runner entry)
- Create: `harnesses/kimi-code/runner/tests/fake_delegate_termreq.py` (fixture)
- Test: `harnesses/kimi-code/runner/tests/test_termination_request.py` (new)
- Test: `harnesses/kimi-code/delegate/tests/test_kill_authority.py` (append tier-b test)

### Interfaces

- Consumes: delegate's `--terminate-request-file` (Task 4).
- Produces:
  - `runner.WRAPPER_GRACE_S = 120`, `runner.TERMINATION_REQUEST_WAIT_S = 60` (module
    constants; env overrides `MP_WRAPPER_GRACE_S` / `MP_TERMINATION_REQUEST_WAIT_S` for tests).
  - `run_delegate(delegate_py, agent, ws, prompt, timeout_s, on_heartbeat=None,
    request_file=None, on_termination=None)` — two new trailing kwargs; existing call
    (`runner.py:999-1001`) updated.
  - `runner._request_termination(proc, request_file, wait_s) -> (outcome, out, err)` with
    `outcome ∈ {"delegate_executed", "unresolved", "request_write_failed",
    "no_request_channel"}`.
  - Journal event `dispatch_termination_requested`:
    `{"event", "for_dispatch_id", "task_id", "reason": "delegate_wrapper_timeout",
    "outcome"}` — **`for_dispatch_id`, never `dispatch_id`** (masking hazard, correction #1).
  - `dispatch_finished` journal record (`runner.py:1024-1030`) and the
    `state["dispatches"]` entry (`runner.py:1004-1013`) each gain
    `"kill_authority": envelope.get("kill_authority")`.
  - Synthetic wedged envelope: `{"status": "timeout", "error": "delegate_wrapper_timeout",
    "kill_authority": "unresolved_reported", "termination_request": outcome,
    "duration_seconds": timeout_s, "agent": agent}`.

### Steps

1. Test-first, part A — flip the registry runner entry in `killauthority.py` to
   `"classification": "external_readonly"`, `"expected": {}`,
   `"must_not_contain": ["proc.kill"]`, note updated to past tense; and append the tier-b
   assertion to `test_kill_authority.py`:

```python
    def test_no_custody_violations_remain(self):
        """TOOL-036 tier-b: no external actor may hold a kill site."""
        bad = [e["site"] for e in killauthority.KILL_SITES
               if e["classification"] == "custody_violation_documented"]
        self.assertEqual(bad, [],
                         f"external actors still hold kill sites: {bad}")
```

   Run the audit — expect **both** new failures (`proc_kill` expected 0 vs found 2;
   `custody_violation_documented` still present):

```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_kill_authority.py -v
```

2. Test-first, part B — write the fixture `harnesses/kimi-code/runner/tests/fake_delegate_termreq.py`:

```python
#!/usr/bin/env python3
"""Fixture fake for the delegate wrapper — TOOL-036 termination-request tests.

Speaks the delegate CLI contract plus --terminate-request-file. Modes
(env FAKE_DELEGATE_MODE):
  honor   poll for the request file; on appearance emit an interrupted
          envelope attributed delegate:runner_requested, exit 130.
  ignore  sleep 30s ignoring the request file (a wedged delegate stand-in).

Env knobs:
  FAKE_DELEGATE_PID_FILE  write own pid here at startup
  FAKE_DELEGATE_SIDECAR   write {"request_file_existed_at_start": bool} here
"""

import argparse
import json
import os
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--terminate-request-file", default=None)
    args = parser.parse_args()

    pid_file = os.environ.get("FAKE_DELEGATE_PID_FILE")
    if pid_file:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
    existed_at_start = bool(args.terminate_request_file
                            and os.path.exists(args.terminate_request_file))
    sidecar = os.environ.get("FAKE_DELEGATE_SIDECAR")
    if sidecar:
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump({"request_file_existed_at_start": existed_at_start}, f)

    if os.environ.get("FAKE_DELEGATE_MODE", "honor") == "honor":
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if (args.terminate_request_file
                    and os.path.exists(args.terminate_request_file)):
                sys.stdout.write(json.dumps({
                    "schema_version": 1, "status": "interrupted",
                    "agent": args.agent, "child_exit_code": None,
                    "child_session_id": None, "child_home": None,
                    "duration_seconds": 0.1, "stdout": "", "stderr": "",
                    "stdout_truncated": False, "stderr_truncated": False,
                    "stdout_log_truncated": False, "stderr_log_truncated": False,
                    "run_dir": None, "acl_warning": False, "job_warning": False,
                    "kill_authority": "delegate:runner_requested",
                    "error": "termination requested via --terminate-request-file",
                }) + "\n")
                sys.stdout.flush()
                return 130
            time.sleep(0.1)
    else:
        time.sleep(30)
    sys.stdout.write(json.dumps({
        "schema_version": 1, "status": "completed", "agent": args.agent,
        "child_exit_code": 0, "child_session_id": None, "child_home": None,
        "duration_seconds": 0.1, "stdout": "", "stderr": "",
        "stdout_truncated": False, "stderr_truncated": False,
        "stdout_log_truncated": False, "stderr_log_truncated": False,
        "run_dir": None, "acl_warning": False, "job_warning": False,
        "kill_authority": "none", "error": None,
    }) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

   and the test `harnesses/kimi-code/runner/tests/test_termination_request.py`:

```python
#!/usr/bin/env python3
"""TOOL-036: the runner is read-only — request, bounded wait, report, never kill.

Run: python -m unittest discover -s runner/tests -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_DIR = ROOT / "runner"
RUNNER = RUNNER_DIR / "runner.py"
FAKE_DELEGATE = Path(__file__).resolve().parent / "fake_delegate_termreq.py"

sys.path.insert(0, str(RUNNER_DIR))
import runner  # noqa: E402

# Fast clocks for the test: budget timeout 1s, wrapper grace 2s, request wait 3s.
ENV_FAST = {"MP_WRAPPER_GRACE_S": "2", "MP_TERMINATION_REQUEST_WAIT_S": "3"}

BUGGY = 'def sum_to_n(n):\n    return sum(range(1, n))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15\n'
    'print("PASS")\n'
)


def run_runner(*argv, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=180)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def make_task(tmp, task_id="t1", timeout_s=1):
    task = {
        "task_id": task_id,
        "prompt": "Fix the bug.",
        "features": {"bounded": True, "known_location": True,
                     "objective_acceptance": True},
        "scope": ["math_utils.py"],
        "verifier": {"argv": ["{python}", "check.py"]},
        "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": timeout_s},
    }
    p = Path(tmp) / "task.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    return str(p)


def make_workspace(tmp):
    ws = Path(tmp) / "ws"
    ws.mkdir()
    (ws / "math_utils.py").write_text(BUGGY, encoding="utf-8")
    (ws / "check.py").write_text(CHECK, encoding="utf-8")
    return str(ws)


def journal_lines(sdir):
    jr = Path(sdir) / "journal.jsonl"
    if not jr.is_file():
        return []
    return [json.loads(ln) for ln in jr.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def _pid_alive(pid):
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    k32.CloseHandle(handle)
    return True


class TerminationRequestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-termreq-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _setup_ready(self):
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp)
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        return ws, task, Path(out["state_dir"])

    def test_request_write_failure_is_reported_not_fatal(self):
        """Review Focus #3: a wedged request channel yields a clean outcome."""
        bad_path = str(Path(self.tmp) / "no" / "such" / "dir" / "req")
        outcome, out, err = runner._request_termination(None, bad_path, 1)
        self.assertEqual(outcome, "request_write_failed")
        self.assertEqual((out, err), ("", ""))
        outcome, _, _ = runner._request_termination(None, None, 1)
        self.assertEqual(outcome, "no_request_channel")

    def test_runner_requests_and_delegate_executes(self):
        ws, task, sdir = self._setup_ready()
        sidecar = Path(self.tmp) / "sidecar.json"
        env = dict(ENV_FAST, FAKE_DELEGATE_MODE="honor",
                   FAKE_DELEGATE_SIDECAR=str(sidecar))
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_DELEGATE), env_extra=env)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["envelope_status"], "interrupted", out)
        lines = journal_lines(sdir)
        term = [e for e in lines
                if e["event"] == "dispatch_termination_requested"]
        self.assertEqual(len(term), 1, lines)
        self.assertEqual(term[0]["outcome"], "delegate_executed")
        self.assertEqual(term[0]["for_dispatch_id"], out["dispatch_id"])
        self.assertNotIn("dispatch_id", term[0],
                         "a bare dispatch_id masks dispatch_open in "
                         "_journal_open (runner.py:579-590)")
        fin = [e for e in lines if e["event"] == "dispatch_finished"]
        self.assertEqual(fin[0]["kill_authority"], "delegate:runner_requested")
        # Stale-file defense (Review Focus #2): the request file did not exist
        # when the delegate started.
        self.assertFalse(
            json.loads(sidecar.read_text())["request_file_existed_at_start"])
        # The open entry paired off: nothing reported in flight.
        status_rc, status_out = run_runner("status", "--workspace", ws)
        self.assertEqual(status_rc, 0, status_out)
        self.assertEqual(status_out["open_journal_ids"], [], status_out)

    @unittest.skipUnless(sys.platform == "win32", "pid liveness probe is Windows-only")
    def test_runner_never_kills_wedged_delegate(self):
        ws, task, sdir = self._setup_ready()
        pid_file = Path(self.tmp) / "fake_delegate.pid"
        env = dict(ENV_FAST, FAKE_DELEGATE_MODE="ignore",
                   FAKE_DELEGATE_PID_FILE=str(pid_file))
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_DELEGATE), env_extra=env)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["envelope_status"], "timeout", out)
        self.assertEqual(out.get("error"), "delegate_wrapper_timeout")
        lines = journal_lines(sdir)
        term = [e for e in lines
                if e["event"] == "dispatch_termination_requested"]
        self.assertEqual(len(term), 1, lines)
        self.assertEqual(term[0]["outcome"], "unresolved")
        fin = [e for e in lines if e["event"] == "dispatch_finished"]
        self.assertEqual(fin[0]["kill_authority"], "unresolved_reported")
        # THE invariant: the runner reported instead of killing — the wedged
        # delegate is still alive after the runner returned.
        pid = int(pid_file.read_text().strip())
        try:
            self.assertTrue(_pid_alive(pid),
                            f"runner killed delegate pid {pid} — read-only "
                            f"contract broken")
        finally:
            # Test-side cleanup of its own fixture is fine.
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True)


if __name__ == "__main__":
    unittest.main()
```

   Run — expect fails (no `dispatch_termination_requested` event, no `kill_authority` keys,
   and the wedged fake is dead instead of alive):

```
python -m unittest discover -s harnesses/kimi-code/runner/tests -p test_termination_request.py -v
```

3. Implement in `runner.py`:

   a. After `DEFAULT_BUDGET` (`:106`) add:

```python
# TOOL-036: the runner is read-only with respect to the worker tree. Past
# timeout + WRAPPER_GRACE_S it REQUESTS termination from the delegate (the
# sole kill authority) and reports the outcome; it never kills the delegate.
# Env overrides exist for hermetic tests.
WRAPPER_GRACE_S = 120
TERMINATION_REQUEST_WAIT_S = 60
```

   b. Replace `run_delegate` (`:708-774`) with the request/report version (this also deletes
      the dead duplicated `if not line:` / undefined-`r` block at `:763-774`, survey
      correction #3):

```python
def _request_termination(proc, request_file, wait_s):
    """Read-only terminal action (TOOL-036): request, bounded wait, never kill.

    Returns (outcome, stdout, stderr). outcome is one of:
      delegate_executed     the delegate ran its kill authority and exited
      unresolved            request delivered; delegate still wedged after wait
      request_write_failed  the request channel itself failed (wedged fs)
      no_request_channel    no request_file was configured for this dispatch
    """
    if not request_file:
        return "no_request_channel", "", ""
    try:
        with open(request_file, "w", encoding="utf-8") as f:
            json.dump({"reason": "delegate_wrapper_timeout",
                       "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
    except OSError:
        return "request_write_failed", "", ""
    try:
        out, err = proc.communicate(timeout=wait_s)
    except subprocess.TimeoutExpired:
        return "unresolved", "", ""
    return "delegate_executed", out, err


def run_delegate(delegate_py, agent, ws, prompt, timeout_s, on_heartbeat=None,
                 request_file=None, on_termination=None):
    """One worker attempt through the delegate wrapper. Returns the envelope.

    A5 (#73): poll the child so a `dispatch_heartbeat` journal record lands at
    least once per interval. TOOL-036: past timeout + WRAPPER_GRACE_S the
    runner REQUESTS termination via request_file and waits up to
    TERMINATION_REQUEST_WAIT_S for the delegate's kill authority to execute
    and emit its own attributed envelope; if the delegate stays wedged the
    runner reports delegate_wrapper_timeout with kill_authority
    unresolved_reported. The runner never kills the delegate."""
    wrapper_grace = float(os.environ.get("MP_WRAPPER_GRACE_S", WRAPPER_GRACE_S))
    request_wait = float(os.environ.get("MP_TERMINATION_REQUEST_WAIT_S",
                                        TERMINATION_REQUEST_WAIT_S))
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(prompt)
        task_file = tf.name
    deadline = time.monotonic() + timeout_s + wrapper_grace
    try:
        cmd = [sys.executable, delegate_py, "--agent", agent, "--workspace", str(ws),
               "--task-file", task_file, "--timeout", str(timeout_s)]
        if request_file:
            # Stale-file defense (TOOL-036): the delegate consumes the file on
            # its first poll; it must not exist before spawn.
            try:
                os.unlink(request_file)
            except OSError:
                pass
            cmd += ["--terminate-request-file", request_file]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
    except OSError:
        try:
            os.unlink(task_file)
        except OSError:
            pass
        raise

    def _on_deadline():
        outcome, o, e = _request_termination(proc, request_file, request_wait)
        if on_termination is not None:
            try:
                on_termination(outcome)
            except OSError:
                pass
        return outcome, o, e

    out, err = "", ""
    try:
        while True:
            remaining = deadline - time.monotonic()
            expired = remaining <= 0
            if not expired:
                try:
                    out, err = proc.communicate(timeout=min(remaining, 10))
                    break
                except subprocess.TimeoutExpired:
                    # A5 (#73): alive-but-slow vs dead must be distinguishable
                    # within one heartbeat interval, not one full timeout.
                    if on_heartbeat is not None:
                        try:
                            on_heartbeat()
                        except OSError:
                            pass
                    expired = time.monotonic() > deadline
            if expired:
                outcome, out, err = _on_deadline()
                if outcome != "delegate_executed":
                    return {"status": "timeout",
                            "error": "delegate_wrapper_timeout",
                            "kill_authority": "unresolved_reported",
                            "termination_request": outcome,
                            "duration_seconds": timeout_s, "agent": agent}
                break  # the delegate emitted its own attributed envelope
    finally:
        try:
            os.unlink(task_file)
        except OSError:
            pass
    line = (out or "").strip().splitlines()
    if not line:
        return {"status": "internal_error", "error": "empty delegate output",
                "stderr": (err or "")[-500:], "agent": agent,
                "kill_authority": "none"}
    try:
        return json.loads(line[-1])
    except json.JSONDecodeError:
        return {"status": "internal_error", "error": "unparseable delegate envelope",
                "stdout_tail": (out or "")[-500:], "agent": agent,
                "kill_authority": "none"}
```

   c. `cmd_dispatch` (`:987-1001`): after `_heartbeat` is defined, add the request path and
      termination callback, and pass them:

```python
    req_path = str(sroot / f"{dispatch_id}.terminate")

    def _termination(outcome):
        # TOOL-036: for_dispatch_id, NOT dispatch_id — _journal_open
        # (:579-590) masks any earlier record sharing dispatch_id, and
        # dispatch_open must stay visible until dispatch_finished pairs it.
        _journal_append(sroot, {
            "event": "dispatch_termination_requested",
            "for_dispatch_id": dispatch_id,
            "task_id": state["task_id"],
            "reason": "delegate_wrapper_timeout",
            "outcome": outcome,
        })

    t0 = time.monotonic()
    envelope = run_delegate(delegate_py, agent, ws, task["prompt"],
                            state["budget"]["timeout_s"],
                            on_heartbeat=_heartbeat,
                            request_file=req_path,
                            on_termination=_termination)
```

   d. Add `"kill_authority": envelope.get("kill_authority"),` to the `state["dispatches"]`
      entry (`:1004-1013`) and to the `dispatch_finished` journal record (`:1024-1030`).

4. Run, expect pass — new suite, the journal regression suite (additive key changes must not
   break `test_dispatch_journal.py`), and the audit:

```
python -m unittest discover -s harnesses/kimi-code/runner/tests -v
python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_kill_authority.py -v
```

5. Commit:

```
git add harnesses/kimi-code/runner/runner.py harnesses/kimi-code/runner/tests/fake_delegate_termreq.py harnesses/kimi-code/runner/tests/test_termination_request.py harnesses/kimi-code/delegate/killauthority.py harnesses/kimi-code/delegate/tests/test_kill_authority.py
git commit -m "fix(runner): request/report instead of proc.kill at wrapper deadline (TOOL-036 #107, task 5)"
```

---

## Task 6 — Codex adapter: attribute its own-child cleanup kills

### Files

- Modify: `harnesses/codex/delegate/delegate.py` (`_cleanup` `:96-114`, call sites
  `:384,:408,:494`)
- Modify: `harnesses/codex/delegate/tests/test_delegate.py` (one assertion)
- Test: `harnesses/codex/delegate/tests/test_cleanup_attribution.py` (new)

### Interfaces

- Produces: `_cleanup(proc: Any, record: Optional[dict] = None) -> int` — when it kills, it
  stamps `record["kill_authority"] = "codex:cleanup_kill"` **at kill time** (before the second
  wait), so the attribution survives a later `CleanupFailure`. `record` is the envelope dict
  under construction: `result` at `:384`, `self.result` at `:408` and `:494` (the
  `_RpcSession` envelope that `run_delegate` returns even on failure — evidenced by
  `test_unreaped_app_session_cannot_report_completed` reading `result["thread_id"]`,
  `test_delegate.py:269-278`).

### Steps

1. Failing tests — create `harnesses/codex/delegate/tests/test_cleanup_attribution.py`:

```python
"""TOOL-036: the codex adapter attributes its own-child cleanup kills."""
import subprocess
import sys
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

import delegate  # noqa: E402


class _FakeProc:
    def __init__(self, wait_results):
        self._wait_results = list(wait_results)
        self.kill_calls = 0
        self.stdin = None
        self.stdout = None

    def wait(self, timeout=None):
        result = self._wait_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def kill(self):
        self.kill_calls += 1


class TestCleanupAttribution(unittest.TestCase):
    def test_kill_is_attributed(self):
        proc = _FakeProc([subprocess.TimeoutExpired("fake", 1), 0])
        record = {}
        self.assertEqual(delegate._cleanup(proc, record=record), 0)
        self.assertEqual(proc.kill_calls, 1)
        self.assertEqual(record["kill_authority"], "codex:cleanup_kill")

    def test_clean_wait_is_not_attributed(self):
        proc = _FakeProc([0])
        record = {}
        self.assertEqual(delegate._cleanup(proc, record=record), 0)
        self.assertEqual(proc.kill_calls, 0)
        self.assertNotIn("kill_authority", record)

    def test_attribution_survives_reap_failure(self):
        """The record is stamped at kill time, before any CleanupFailure."""
        proc = _FakeProc([subprocess.TimeoutExpired("fake", 1), OSError("wedged")])
        record = {}
        with self.assertRaises(delegate.CleanupFailure):
            delegate._cleanup(proc, record=record)
        self.assertEqual(record["kill_authority"], "codex:cleanup_kill")


if __name__ == "__main__":
    unittest.main()
```

   and add one line to the existing `test_unreaped_app_session_cannot_report_completed`
   (`test_delegate.py:269-278`), after `self.assertEqual(proc.kill_calls, 1)`:
   `self.assertEqual(result["kill_authority"], "codex:cleanup_kill")`.

2. Run, expect fail (`TypeError: _cleanup() got an unexpected keyword argument 'record'`,
   then `KeyError: 'kill_authority'`):

```
python -m unittest discover -s harnesses/codex/delegate/tests -v
```

3. Implement in `harnesses/codex/delegate/delegate.py`:

```python
def _cleanup(proc: Any, record: Optional[dict] = None) -> int:
    active_error = sys.exc_info()[1]
    _close_pipe(proc.stdin)
    try:
        try:
            code = proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            # TOOL-036: this adapter owns only its direct child (no Job
            # Object). Stamp the attribution at kill time so it survives a
            # later CleanupFailure; record is the envelope under construction.
            if record is not None:
                record["kill_authority"] = "codex:cleanup_kill"
            code = proc.wait(timeout=1)
        if not isinstance(code, int):
            raise CleanupFailure()
        return code
    except Exception as exc:
        if isinstance(active_error, TransportTimeout):
            raise active_error from exc
        raise CleanupFailure() from exc
    finally:
        _close_pipe(proc.stdout)
```

   Call sites: `:384` → `code = _cleanup(proc, record=result)`; `:408` →
   `_cleanup(self.proc, record=self.result)`; `:494` → `_cleanup(self.proc, record=self.result)`.

4. Run, expect pass (whole codex suite — the stalled-IO/timeout tests at
   `test_delegate.py:215-298` must stay green):

```
python -m unittest discover -s harnesses/codex/delegate/tests -v
```

5. Commit:

```
git add harnesses/codex/delegate/delegate.py harnesses/codex/delegate/tests/test_delegate.py harnesses/codex/delegate/tests/test_cleanup_attribution.py
git commit -m "feat(codex): attribute own-child cleanup kills in envelope (TOOL-036 #107, task 6)"
```

---

## Task 7 — Evals harness: attribute its timeout taskkill in the result row

### Files

- Modify: `evals/run_eval.py` (extract the result-row builder from `launch_kimi` `:116-123`)
- Test: `evals/tests/test_kill_attribution.py` (new)

### Interfaces

- Produces: `run_eval._result_row(started_at, wall, agent_exit, timed_out, est_tokens,
  tokens_reported) -> dict` — the existing row shape plus
  `"kill_authority": "eval:taskkill_tree_force" if timed_out else "none"`. `launch_kimi`
  returns `_result_row(...)`.

### Steps

1. Failing test — create `evals/tests/test_kill_attribution.py`:

```python
#!/usr/bin/env python3
"""TOOL-036: eval result rows attribute the harness's own timeout taskkill."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_eval  # noqa: E402


class TestEvalKillAttribution(unittest.TestCase):
    def test_timeout_row_attributes_eval_taskkill(self):
        row = run_eval._result_row("2026-09-25T00:00:00", 12.3, -1, True, 100, None)
        self.assertEqual(row["kill_authority"], "eval:taskkill_tree_force")
        self.assertEqual(row["agent_exit"], -1)
        self.assertTrue(row["timed_out"])

    def test_clean_row_attributes_none(self):
        row = run_eval._result_row("2026-09-25T00:00:00", 1.004, 0, False, 10, None)
        self.assertEqual(row["kill_authority"], "none")
        self.assertEqual(row["wall_clock_s"], 1.0)


if __name__ == "__main__":
    unittest.main()
```

2. Run, expect fail (`AttributeError: module 'run_eval' has no attribute '_result_row'`):

```
python -m unittest discover -s evals/tests -p test_kill_attribution.py -v
```

3. Implement in `evals/run_eval.py` — replace the literal dict at `:116-123` with:

```python
def _result_row(started_at, wall, agent_exit, timed_out, est_tokens, tokens_reported):
    return {
        "started_at": started_at,
        "wall_clock_s": round(wall, 2),
        "agent_exit": agent_exit,
        "timed_out": timed_out,
        # TOOL-036: this harness owns the kimi.exe tree it spawned (no Job
        # Object); the timeout taskkill at :95-98 is its own-child cleanup
        # authority, attributed here.
        "kill_authority": "eval:taskkill_tree_force" if timed_out else "none",
        "est_tokens": est_tokens,
        "tokens_reported": tokens_reported,
    }
```

   and make `launch_kimi` end with
   `return _result_row(started_at, wall, agent_exit, timed_out, est_tokens, tokens_reported)`.

4. Run, expect pass — new test plus the whole evals suite:

```
python -m unittest discover -s evals/tests -v
```

5. Commit:

```
git add evals/run_eval.py evals/tests/test_kill_attribution.py
git commit -m "feat(evals): attribute timeout taskkill in result rows (TOOL-036 #107, task 7)"
```

---

## Task 8 — Human code table, AGENTS.md layout line, full-suite evidence

### Files

- Create: `docs/kill-authority.md`
- Modify: `AGENTS.md` (the `harnesses/kimi-code/delegate/` layout bullet, lines 16-17)

### Steps

1. Write `docs/kill-authority.md`. It must mirror the final `KILL_SITES` registry (generate
   the table from the registry by hand and keep the two in sync — the audit test enforces the
   registry against source, this document explains it to humans):

```markdown
# Kill Authority (TOOL-036, issue #107)

One component terminates a delegate-owned worker tree: the delegate that owns
its Job Object and process handles, via `killauthority.KillAuthority`.
External actors are read-only: their terminal action is a termination request
plus a report, never a kill.

## Attribution vocabulary (`kill_authority` key)

| Value | Meaning |
|---|---|
| `none` | Payload root exited on its own; no kill occurred. |
| `delegate:timeout` | Delegate authority terminated the tree at the dispatch deadline. |
| `delegate:interrupted` | Delegate authority terminated the tree on SIGINT/SIGTERM/KeyboardInterrupt. |
| `delegate:runner_requested` | Delegate authority terminated the tree after the runner wrote the terminate-request file at the wrapper deadline. Envelope status is `interrupted`, exit 130. |
| `unresolved_reported` | Runner requested; the delegate never confirmed within TERMINATION_REQUEST_WAIT_S. The runner did NOT kill anything; the wedged delegate and its tree remain the delegate's custody (operator action via `status`/orphan reporting). |
| `codex:cleanup_kill` | Codex adapter killed its own direct child after a 1s grace (no Job Object custody). |
| `eval:taskkill_tree_force` | Evals harness taskkill /T /F'd the kimi.exe tree it spawned itself. |

Attribution records what the authority DID. For `allow_breakaway` agents it
is not a guarantee that detached descendants died (delegate.py:95-107).

## Kill-path inventory (kept in sync with killauthority.KILL_SITES)

| Site | File | Class | Mechanism | Record |
|---|---|---|---|---|
| KillAuthority.terminate | harnesses/kimi-code/delegate/killauthority.py | authority | taskkill /T → grace → /T /F → job close → /T /F | envelope kill_authority |
| KillAuthority.release | same | authority (custody) | job close reaps stragglers on normal exit | kill_authority "none" |
| Runner wrapper deadline | harnesses/kimi-code/runner/runner.py | external_readonly | writes request file, bounded wait, journals outcome | journal dispatch_termination_requested + dispatch_finished.kill_authority |
| Kernel on delegate death | (OS) | authority (custody) | KILL_ON_JOB_CLOSE fires when the delegate's job handle closes for any reason | implicit; documented here |
| Codex cleanup | harnesses/codex/delegate/delegate.py:96-114 | own_child_cleanup | proc.kill() on own direct child after 1s grace | envelope kill_authority |
| Evals timeout | evals/run_eval.py:95-98 | own_child_cleanup | taskkill /T /F on own kimi.exe | result row kill_authority |
| Cascade backstop | harnesses/kimi-code/cascade/cascade.py:476-483 | frozen_artifact | stdlib subprocess.run timeout kill of direct child | frozen; envelope passthrough |
| plain_arm / pilot / zproctor spawns | evals/plain_arm.py:63-67, runner/pilot.py:69-73, zcode/zproctor.py:41-53 | stdlib_implicit | stdlib timeout kills of own direct children | not attributed (no envelope surface) |
| core/* | core/decisions.py, core/task_schema.py | policy_pure | none — pure functions | n/a |

## Residuals

- A delegate wedged INSIDE its kill sequence (worst case ≈ grace + 55s of
  swallowed taskkill timeouts) may outlast the runner's request wait; the
  journal then says unresolved even though the authority later executed. The
  record never claims the runner killed it. Not hermetically testable.
- The delegate consumes a terminate-request file on first poll with no
  staleness check; defense is the runner's per-dispatch uuid path +
  unlink-before-spawn.
- _journal_open (runner.py:579-590) masks dispatch_open behind any newer
  record sharing dispatch_id (heartbeats do this today) — follow-up issue,
  out of TOOL-036 scope.
```

2. Update the `AGENTS.md` layout bullet (lines 16-17) to read:

```
- `harnesses/kimi-code/delegate/` — the wrapper (`delegate.py`), the single kill authority
  (`killauthority.py`, TOOL-036: sole terminator of delegate-owned worker trees, plus the
  repo-wide `KILL_SITES` registry), live config (`agents.json`), annotated example
  (`agents.example.json`), tests (`tests/test_delegate.py`), docs (`README.md`).
```

3. Run every suite, exactly as CI does (`ci.yml:25-41`) — all eight must pass:

```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
python -m unittest discover -s harnesses/codex/delegate/tests -v
python -m unittest discover -s harnesses/codex/skill/model-proctor/tests -v
python -m unittest discover -s harnesses/kimi-code/runner/tests -v
python -m unittest discover -s core/tests -v
python -m unittest discover -s scripts/tests -v
python -m unittest discover -s evals/tests -v
python -m unittest discover -s harnesses/zcode/tests -v
```

   plus the flat-install smoke from `ci.yml:42-47` (same `python -c` command as Task 3 step 4).

4. Commit:

```
git add docs/kill-authority.md AGENTS.md
git commit -m "docs: kill-authority code table and layout note (TOOL-036 #107, task 8)"
```

5. Close-out: paste the eight-suite output into issue #107's
   `## Final evidence and handoff` section (`AGENTS.md:131-136` requires it before closing).

---

## Self-review pass

**Spec coverage** (ticket asks → where satisfied):
- Full kill-path inventory as a code table → `KILL_SITES` (Task 1) + `docs/kill-authority.md`
  (Task 8), enforced against source by the audit test both directions.
- Enforcement mechanism (handle/pid ownership) → `KillAuthority` owns pid/job/proc handle via
  DI (Task 2), delegate rewired (Task 3), audit tier-b asserts no custody violations remain
  (Task 5).
- External actors read-only (terminal action = report, never kill) → runner rewired to
  request/wait/report with the still-alive assertion as the executable invariant (Task 5);
  cascade frozen-registered; codex/evals kill only their own direct children, now attributed
  (Tasks 6-7).
- Every payload death attributed to exactly one authority in the run record → envelope
  `kill_authority` (Task 3), journal `dispatch_finished.kill_authority` +
  `dispatch_termination_requested` outcome (Task 5), codex envelope (Task 6), eval row
  (Task 7). `unresolved_reported` is itself a single attribution: none executed.

**Placeholder scan**: every task contains runnable test code, runnable implementation code,
exact commands, exact commit lines. No TBDs. Where a first run could theoretically diverge
(surveyed grep counts in Task 1), the plan states the surveyed values *and* the stop-and-
re-survey rule rather than a placeholder.

**Type consistency across tasks**: `kill_authority` is `str` everywhere
(`_ENVELOPE_KEYS` entry `"kill_authority": str`; journal value may be `null` for legacy
fakes without the key — `envelope.get("kill_authority")` is intentional, matching the
existing nullable-key pattern in `dispatch_finished`). Attribution strings are drawn only
from `killauthority.ATTRIBUTIONS`: `none` (Tasks 3, 5 synthetic parse-failure envelopes),
`delegate:timeout`/`delegate:interrupted`/`delegate:runner_requested` (Task 3/4, from
`TERMINATE_REASONS`), `unresolved_reported` (Task 5), `codex:cleanup_kill` (Task 6),
`eval:taskkill_tree_force` (Task 7). `terminate()` reasons stay lowercase nouns; the
`delegate:` prefix is added by `terminate`, never by callers.

**Review-focus pinning**: #1 → Task 5 (`test_runner_never_kills_wedged_delegate`; residual
documented Task 8). #2 → Task 4 (`test_stale_request_file_kills_immediately`) + Task 5
(sidecar assertion in `test_runner_requests_and_delegate_executes`). #3 → Task 5
(`test_request_write_failure_is_reported_not_fatal`). #4 → Task 2
(`test_terminate_without_job_still_kills_and_attributes`) + Task 3 regression
(`test_job_warning_false_on_happy_path`). #5 → Task 2 (docstring) + Task 3 regression
(`TestBreakawayJobFlags` green in the full-suite run).

**Known accepted deltas**: envelope gains one key (additive; `assert_envelope_schema` only
checks presence/type of listed keys, `test_delegate.py:336-342`); frozen cascade treats
`interrupted` as non-terminal for rollback (`cascade.py:121`) — acceptable, cascade is not
the live path; runner returns from a wedged dispatch while the delegate process lingers (its
own 64KB-pipe/stdio fate is unchanged from today, when it would have been killed — this is
the deliberate behavior change the ticket asks for, surfaced by `status`/orphan reporting).
