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
        "must_not_contain": ["kill_process_tree"],
        "must_contain": ["import killauthority"],
        "note": "Every exit path routes through killauthority.KillAuthority "
                "(TOOL-036); the old inlined kill_process_tree was deleted "
                "when the wiring landed.",
    },
    {
        "site": "runner wrapper-deadline kill",
        "file": "harnesses/kimi-code/runner/runner.py",
        "classification": "external_readonly",
        "expected": {},
        "must_not_contain": ["proc.kill"],
        "note": "Read-only since TOOL-036 task 5: past the derived runner "
                "breaker (timeout_stack.runner_breaker_s, #108) the runner "
                "writes a terminate-request file, "
                "waits TERMINATION_REQUEST_WAIT_S, and reports the outcome "
                "(dispatch_termination_requested + kill_authority). It never "
                "kills the delegate.",
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
        "expected": {"taskkill": 2},
        "note": "taskkill /T /F at run_eval.py:95-98 on a kimi.exe the eval "
                "harness spawned itself (no Job Object); attributed as "
                "eval:taskkill_tree_force in the result row (TOOL-036 task 7). "
                "The second taskkill match is the attribution comment in "
                "_result_row.",
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
    is the feature breakaway buys (delegate.py BREAKAWAY_OK design comment).

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
