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
#: residual comment at delegate.py's BREAKAWAY_OK design note).
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
                "(delegate.py:928-989); Task 3 funnels it into KillAuthority "
                "and flips these markers.",
    },
    {
        "site": "runner wrapper-deadline kill",
        "file": "harnesses/kimi-code/runner/runner.py",
        "classification": "custody_violation_documented",
        "expected": {"proc_kill": 3},
        "note": "proc.kill() on the delegate at the wrapper deadline "
                "(runner.py:891 and :907) reaches into delegate custody; the "
                "delegate's job close then wipes the worker tree. The third "
                "match is the #103 design comment at runner.py:874. Task 5 "
                "removes all three; the runner becomes request/report-only.",
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
