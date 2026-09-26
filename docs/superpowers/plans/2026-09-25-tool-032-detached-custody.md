# Detached-Payload Custody Implementation Plan

**Planning status resolution:** this document IS the planning that TOOL-032 / dachent/model_proctor#103 required. Executing the tasks below closes the ticket's planning phase; no separate planning artifact is owed.

Executors: use the `subagent-driven-development` skill — one subagent per task, in task order; each task is self-contained (files, interfaces, tests, code, commit).

## Goal

A worker launched for an agent configured `allow_breakaway: true` ("detached payload") must survive the death of its launcher — specifically the runner's `proc.kill()` of the delegate (`harnesses/kimi-code/runner/runner.py:738,754`), which today collapses the whole worker tree because the delegate's `KILL_ON_JOB_CLOSE` job handle closes with the delegate process (#103 root mechanism). Breakaway becomes the DEFAULT and ENFORCED behavior for detached payloads, the dispatch FAILS LOUD when detached custody cannot be established (no silent fallback into the kill-on-close job), and the escape mechanism is the greenfield WMI `Win32_Process.Create` launch, which parents the payload to the WMI provider service so it never enters the delegate's job at all.

## Architecture

Today `allow_breakaway: true` only ORs `JOB_OBJECT_LIMIT_BREAKAWAY_OK` into the job's LimitFlags (`delegate.py:1203`) — that flag merely *permits* a child to call `CreateProcess(CREATE_BREAKAWAY_FROM_JOB)`, which Python's `subprocess` never does, so the shipped flag is a near-no-op for Python workers and the payload still dies with the job. The fix adds a second, real launch path: when `allow_breakaway` is true, the delegate writes a JSON launch spec into the ACL-hardened run_dir and WMI-spawns a tiny stdlib bootstrap (`python -c ...`) that re-creates the exact child environment (including the isolated `KIMI_CODE_HOME` and the `_CHILD_MARKER` anti-nesting flag), redirects stdout/stderr to the run_dir logs, and execs the worker. The delegate supervises the bootstrap through a `SYNCHRONIZE` handle (poll/`WaitForSingleObject`, same 0.1s cadence, same timeout/interrupt kill via `kill_process_tree(pid, grace, job=None)`), so budgets, envelopes, and exit codes are unchanged.

## Tech Stack

- Python 3.10, **standard library only** (AGENTS.md:115-116). WMI is reached via a `subprocess.run` call to `powershell.exe -NoProfile -NonInteractive -Command "Invoke-CimMethod ..."` — the same `_MINIMAL_TOOL_ENV` pattern already used for taskkill/icacls (`delegate.py:79-87`). No WMI Python package exists or may be added.
- Windows Job Objects via the existing `ctypes` kernel32 block (`delegate.py:93-227`).
- Tests: stdlib `unittest`, suite dir `harnesses/kimi-code/delegate/tests/`; CI is `windows-latest` only (`.github/workflows/ci.yml`), so Win32-only tests use `@unittest.skipUnless(sys.platform == "win32", ...)`.
- Repo: `dachent/model_proctor`, worktree `mp_wt/`, branch `tool-032-detached-custody` (already checked out at 8982e24).

## Spec

- Ticket: **dachent/model_proctor issue #103 / TOOL-032** — "Detached-payload custody".
- Inventory: `mp_inventory_103-110.md` at the session root (§1 launch paths, §2 kill paths, #103 plan-feeding notes). Verified against the worktree; corrections at the end of this plan.
- Launch-path audit (performed during planning, verified by reading the code):
  - `harnesses/kimi-code/delegate/delegate.py:1172-1180` — the ONLY worker-launch path in the kimi harness (`subprocess.Popen`); job assign at `:1198-1218`. **This is the path that gains the WMI branch.**
  - `harnesses/kimi-code/runner/runner.py:723-726` — spawns the delegate with no job object. Correct as-is: for contained workers the delegate's own job must collapse when the delegate dies; a runner-side job would add nothing. Gains a documenting comment only (Task 4).
  - `harnesses/kimi-code/cascade/cascade.py:476-478` — frozen research artifact, not production authority (AGENTS.md:24-30). Out of scope.
  - `evals/run_eval.py:86-98`, `evals/plain_arm.py:63-67`, `harnesses/codex/delegate/delegate.py:582-584`, `harnesses/zcode/zproctor.py:41-53` — peripheral spawns with no job objects and no `allow_breakaway` surface. Out of scope (eval tooling and a separate adapter; #103's production path is kimi delegate only).
  - Repo-wide grep confirms **zero** existing WMI/`Win32_Process.Create` code — the helper is greenfield, as the inventory states.

## Global Constraints

- Stdlib-only; no new dependencies, no new suites beyond the existing delegate tests dir.
- The job-contained launch path (agents without `allow_breakaway`) must be byte-for-byte behavior-identical: same envelope, same kill sequence, same `job_warning` degradation on job-setup failure.
- `resolve_model_agent` keeps forcing `allow_breakaway = False` (`delegate.py:365`); the template guard (`delegate.py:444-447`) stays. Model-mode dispatch can never detach.
- Envelope schema (`_ENVELOPE_KEYS` in `test_delegate.py:314-333`) is unchanged — no new keys, no removed keys.
- Repo convention (AGENTS.md:121): **no git mutations without explicit user confirmation.** The commit commands below are exact and mandatory in content, but the executor runs each one only after the supervising workflow's confirmation gate.
- Commits: `git add` explicit paths only, one commit per task, messages as given (repo style: lowercase area prefix, issue ref).
- Per-task verification: `python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_breakaway.py -v` from the repo root; before EACH commit also run the full delegate suite: `python -m unittest discover -s harnesses/kimi-code/delegate/tests -v`.

## Review Focus

The five failure classes this spec implies that NO task's tests exercise — review these first; they are the most likely to bite:

1. **WMI provider degradation.** `Winmgmt` service stopped, CIM provider wedged, or a transient nonzero `ReturnValue`. The dispatch fails loud (`internal_error`) by design, but no test simulates a *service-level* outage — only a bad executable (Task 1) and a mocked helper (Task 3). A reviewer must confirm the fail-loud path cannot fall through to a contained launch on ANY exception type (the `except CustodyError` in `_run_detached_dispatch` must not have a bare-`except` neighbor that swallows into the Popen path).
2. **Bootstrap spec confidentiality.** `detach_spec.json` carries the full `child_env`, which can include credentials injected via the agent's `environment` map. It is written into the ACL-hardened run_dir, but if `create_run_dir` set `acl_warning=True` the spec is weakly protected and nothing escalates. No test covers this; the reviewer decides whether `acl_warning` on a detached dispatch should upgrade to failure (recommend: not in this ticket — note it in the commit message and let #103's follow-up decide).
3. **PID-reuse window.** WMI returns a PID; if the bootstrap dies instantly and the PID is reused before `open_waitable_process`, the delegate could wait on an unrelated process. The window is milliseconds and the handle open is immediate, but no test can force it — reviewer confirms `reap_handle` STILL_ACTIVE (259) handling treats a reused, living PID as "still running" (acceptable, bounded by timeout) rather than fabricating an exit code.
4. **Unbounded detached logs.** The contained path enforces `max_log_bytes` live via `OutputReader`; the detached path enforces `max_stdout_bytes`/`max_stderr_bytes` only at read-back time. A runaway payload can fill the disk between launch and deadline. No test; reviewer confirms the residual is documented in the function docstring (it is, in Task 3's code).
5. **Post-launcher-death timeout enforcement.** If the RUNNER kills the delegate, nothing enforces the payload's own `--timeout` anymore — the payload runs to natural exit (this is the documented cost at `delegate.py:95-107`: "surviving the run is the feature, and outliving a timeout is its cost"). The regression test (Task 4) cleans up its 300s sleeper explicitly; no test asserts an orphaned payload ever dies. Reviewer confirms the docstring and the `#103` design comment say this plainly.

---

## Task 1 — WMI detached-spawn primitive (`wmi_spawn_detached` + waitable-handle helpers)

### Files
- Modify: `harnesses/kimi-code/delegate/delegate.py`
- Test: `harnesses/kimi-code/delegate/tests/test_breakaway.py`

### Interfaces
Produces (consumed by Tasks 3-4):
```python
class CustodyError(Exception)            # delegate.py, near ConfigError (:234)
def wmi_spawn_detached(command_line: str, working_dir: str, timeout_s: float = 30) -> int
    # Returns new PID. Raises CustodyError on ANY failure. Never falls back.
def open_waitable_process(pid: int) -> "HANDLE"          # Windows-only block
def reap_handle(handle, timeout_ms: int) -> "int | None" # exit code, or None if still running
```
Consumes: existing `_resolve_system_tool` (`delegate.py:74-77`), `_MINIMAL_TOOL_ENV` (`:82-87`), `_k32`/`wintypes`/`_PROCESS_QUERY_LIMIT_INFORMATION` (`:111-137`), `close_process_handle` (`:218-219`).

### Steps

1. Write the failing tests. In `harnesses/kimi-code/delegate/tests/test_breakaway.py`, extend the imports at `:13-15` and append a new class before the `if __name__` block:

```python
import shutil
import tempfile
import time
import unittest.mock
```
(the existing file already imports `sys`, `unittest`, `Path`; add the four lines above)

```python
@unittest.skipUnless(_IS_WINDOWS, "WMI launch is Windows-only")
class TestWmiSpawn(unittest.TestCase):
    """wmi_spawn_detached: real Win32_Process.Create round-trips, read back
    from the kernel (pid + exit code), not from the PowerShell stdout alone."""

    def test_spawn_returns_live_pid_and_exit_code(self):
        pid = delegate.wmi_spawn_detached("cmd.exe /c exit 0", tempfile.gettempdir())
        self.assertIsInstance(pid, int)
        self.assertGreater(pid, 0)
        handle = delegate.open_waitable_process(pid)
        try:
            rc = None
            deadline = time.time() + 30
            while rc is None and time.time() < deadline:
                rc = delegate.reap_handle(handle, 500)
            self.assertEqual(rc, 0, "cmd /c exit 0 must reap with exit code 0")
        finally:
            delegate.close_process_handle(handle)

    def test_bad_command_raises_custody_error(self):
        # Win32_Process.Create returns nonzero ReturnValue for a missing exe;
        # the helper must raise, never return a bogus pid or fall back.
        with self.assertRaises(delegate.CustodyError):
            delegate.wmi_spawn_detached(
                r"C:\no\such\exe-mp103-zzz.exe /c exit 0", tempfile.gettempdir())

    def test_bad_powershell_raises_custody_error(self):
        with unittest.mock.patch.object(
                delegate, "_POWERSHELL_EXE", r"C:\no\such\powershell-zzz.exe"):
            with self.assertRaises(delegate.CustodyError):
                delegate.wmi_spawn_detached("cmd.exe /c exit 0", tempfile.gettempdir())
```

2. Run, expect fail (all three: `AttributeError: module 'delegate' has no attribute ...`):
```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_breakaway.py -v
```

3. Implement in `harnesses/kimi-code/delegate/delegate.py`.

(a) Next to `_TASKKILL_EXE`/`_ICACLS_EXE` (`:79-80`):
```python
_POWERSHELL_EXE = (_resolve_system_tool(os.path.join("WindowsPowerShell", "v1.0", "powershell.exe"))
                   if _IS_WINDOWS else "powershell")
```

(b) Next to `ConfigError` (`:234`):
```python
class CustodyError(Exception):
    """Raised when detached-payload custody cannot be established or verified.

    Distinct from ConfigError/InputError: custody failures are runtime launch
    failures and map to an internal_error envelope, never to a silent fallback
    into the kill-on-close job (TOOL-032, #103).
    """
```

(c) Module level, after `wmi_spawn_detached`'s future home (place the PS script constant just above the function, after `kill_process_tree` ends at `:926`):
```python
_WMI_CREATE_PS = (
    "$ErrorActionPreference = 'Stop'; "
    "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
    "-Arguments @{ CommandLine = $env:MP_DETACH_CL; CurrentDirectory = $env:MP_DETACH_CWD }; "
    "Write-Output (ConvertTo-Json -Compress -InputObject "
    "@{ ProcessId = $r.ProcessId; ReturnValue = $r.ReturnValue })"
)


def wmi_spawn_detached(command_line, working_dir, timeout_s=30):
    """Spawn a process via WMI Win32_Process.Create. Returns the new PID.

    The new process is parented to the WMI provider service, so it never
    enters this delegate's job object and survives the delegate's death —
    the actual escape that JOB_OBJECT_LIMIT_BREAKAWAY_OK only *permits*
    (and Python's subprocess never requests). The command line travels via
    the MP_DETACH_CL / MP_DETACH_CWD environment variables to avoid nested
    PowerShell quoting; the launcher runs under _MINIMAL_TOOL_ENV like
    taskkill/icacls. Raises CustodyError on any failure: there is no
    contained fallback for a caller that asked for detachment.
    """
    if not _IS_WINDOWS:
        raise CustodyError("WMI detached launch is Windows-only")
    env = dict(_MINIMAL_TOOL_ENV)
    env["MP_DETACH_CL"] = command_line
    env["MP_DETACH_CWD"] = working_dir
    try:
        out = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-NonInteractive", "-Command", _WMI_CREATE_PS],
            capture_output=True, timeout=timeout_s, env=env, text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CustodyError(f"WMI launcher invocation failed: {type(e).__name__}: {e}")
    lines = (out.stdout or "").strip().splitlines()
    try:
        payload = json.loads(lines[-1])
        pid = int(payload["ProcessId"])
        rv = int(payload["ReturnValue"])
    except (ValueError, KeyError, TypeError, IndexError):
        raise CustodyError(
            "WMI launcher returned unparseable output: "
            f"stdout={(out.stdout or '')[-200:]!r} stderr={(out.stderr or '')[-200:]!r}")
    if rv != 0 or pid <= 0:
        raise CustodyError(f"Win32_Process.Create failed: ReturnValue={rv}")
    return pid
```

(d) Inside the `if _IS_WINDOWS:` ctypes block, after `is_pid_alive` (`:221-227`), add the constant (next to `:111` is also acceptable) and the two helpers:
```python
    _SYNCHRONIZE = 0x00100000

    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD

    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.GetExitCodeProcess.restype = wintypes.BOOL

    def open_waitable_process(pid):
        """Open a SYNCHRONIZE|query handle on pid. Raises CustodyError if it cannot."""
        handle = _k32.OpenProcess(
            _SYNCHRONIZE | _PROCESS_QUERY_LIMIT_INFORMATION, False, pid)
        if not handle:
            raise CustodyError(
                f"OpenProcess({pid}) failed: {ctypes.WinError(ctypes.get_last_error())}")
        return handle

    def reap_handle(handle, timeout_ms):
        """Wait up to timeout_ms for the process. Returns its exit code, or None if still running."""
        if _k32.WaitForSingleObject(handle, timeout_ms) != 0:  # WAIT_OBJECT_0
            return None
        code = wintypes.DWORD(0)
        if not _k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise CustodyError(
                f"GetExitCodeProcess failed: {ctypes.WinError(ctypes.get_last_error())}")
        return code.value
```

4. Run, expect pass (3 new tests + the existing 7):
```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_breakaway.py -v
```
Then the full delegate suite, expect all green:
```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
```

5. Commit (after the repo's user-confirmation gate):
```
git add harnesses/kimi-code/delegate/delegate.py harnesses/kimi-code/delegate/tests/test_breakaway.py docs/superpowers/plans/2026-09-25-tool-032-detached-custody.md
git commit -m "delegate: add WMI Win32_Process.Create detached-spawn primitive (TOOL-032, #103)"
```

---

## Task 2 — Validator: `allow_breakaway` forbids `stdin` prompt delivery

### Files
- Modify: `harnesses/kimi-code/delegate/delegate.py`
- Test: `harnesses/kimi-code/delegate/tests/test_breakaway.py`

### Interfaces
Consumes: `_validate_agent(name, agent, global_max_timeout, check_executable=False)` (`delegate.py:450`); the `pd` local already bound at `:472`; the `ab` local already bound at `:532`.
Produces: no new symbols. Behavior contract relied on by Task 3: any config that reaches `_run_detached_dispatch` has `prompt_delivery in ("argument", "file")`.

### Steps

1. Write the failing test. In `test_breakaway.py`, add to `TestBreakawayValidation` (after `test_absent_defaults_to_false`, `:52-56`):

```python
    def test_stdin_delivery_rejected_with_breakaway(self):
        """WMI launch cannot pipe stdin; a breakaway+stdin config must be
        refused at load, not discovered as a hung child at dispatch."""
        a = make_agent(self.echo_script)  # prompt_delivery="stdin"
        a["allow_breakaway"] = True
        cfg = self._config({"test-agent": a})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = self._assert_result(out, err, rc, "invalid", 64)
        self.assertIn("prompt_delivery", result["error"])
```

2. Modify the existing `test_true_accepted` (`:42-45`) — with the Task-3 dispatch change, an accepted breakaway config must use a deliverable channel; keep this test a *validation* acceptance by switching the fixture to argument delivery and making it Windows-only (dispatch of a breakaway agent now requires WMI):

```python
    @unittest.skipUnless(_IS_WINDOWS, "breakaway dispatch requires WMI (Windows-only)")
    def test_true_accepted(self):
        a = make_agent(self.echo_script)
        a["prompt_delivery"] = "argument"
        a["allow_breakaway"] = True
        cfg = self._config({"test-agent": a})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "completed", 0)
```

3. Run, expect fail: `test_stdin_delivery_rejected_with_breakaway` gets `completed/0` instead of `invalid/64`; `test_true_accepted` fails because Task 3 does not exist yet (delegate returns `internal_error` — no, without Task 3 the WMI branch doesn't exist, so the old contained path runs and `test_true_accepted` PASSES against unmodified code; the failing signal at this step is the stdin test only). Expected red: exactly one failure, `test_stdin_delivery_rejected_with_breakaway`.
```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_breakaway.py -v
```

4. Implement in `delegate.py` `_validate_agent`, immediately after the breakaway bool check (`:532-534`):

```python
    if ab and pd == "stdin":
        raise ConfigError(
            f"Agent '{name}': allow_breakaway requires prompt_delivery "
            f"'argument' or 'file' — detached WMI launch (TOOL-032) cannot "
            f"pipe stdin to a process parented outside the job")
```

5. Run, expect pass (stdin test green; `test_true_accepted` still passing via the legacy contained path):
```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_breakaway.py -v
python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
```

6. Commit (after confirmation):
```
git add harnesses/kimi-code/delegate/delegate.py harnesses/kimi-code/delegate/tests/test_breakaway.py
git commit -m "delegate: reject allow_breakaway with stdin prompt delivery (TOOL-032, #103)"
```

---

## Task 3 — Detached dispatch path: `_run_detached_dispatch` + run_delegate branch

### Files
- Modify: `harnesses/kimi-code/delegate/delegate.py`
- Test: `harnesses/kimi-code/delegate/tests/test_breakaway.py`

### Interfaces
Produces:
```python
_DETACH_BOOTSTRAP: str   # module-level; the python -c body the WMI child runs

def _run_detached_dispatch(agent_name, agent, cfg, argv, workspace, child_env,
                           child_home, run_dir, acl_warning, timeout, start_time):
    # Returns (result_dict, exit_code) with the same envelope contract as the
    # contained path: completed/failed -> EXIT_OK, timeout -> EXIT_TIMEOUT,
    # interrupted -> EXIT_INTERRUPTED, custody failure -> internal_error/EXIT_INTERNAL.

def _read_log_capped(path, cap):
    # Returns (text, truncated_bool): first `cap` bytes decoded utf-8/replace.
```
Consumes (all Task 1): `wmi_spawn_detached`, `open_waitable_process`, `reap_handle`, `CustodyError`. Also existing: `_make_result` (`:1403-1427`), `kill_process_tree` (`:865-926`), `extract_child_session_id` (`:647-661`), `_interrupted` (`:933`), `close_process_handle`, `EXIT_*` (`:49-53`).

### Steps

1. Write the failing tests. In `test_breakaway.py`, extend the existing import line `from test_delegate import DelegateTestBase, make_agent` (`:22`) to:

```python
from test_delegate import (  # noqa: E402
    DelegateTestBase, make_agent, _ECHO_ARG, _EXIT_CODE, _PID_SLEEPER,
)
```

Append two classes before the `if __name__` block:

```python
class TestDetachedFailLoud(DelegateTestBase):
    """Fail-loud contract: when detached custody cannot be established the
    dispatch is internal_error and NOTHING is launched — never a silent
    fallback into the kill-on-close job (#103)."""

    def test_wmi_failure_is_internal_error_and_launches_nothing(self):
        agent = make_agent(self.echo_script, prompt_delivery="argument")
        agent["allow_breakaway"] = True
        run_dir, acl_warning = delegate.create_run_dir()
        try:
            with unittest.mock.patch.object(
                    delegate, "wmi_spawn_detached",
                    side_effect=delegate.CustodyError("simulated wmi outage")):
                result, exit_code = delegate._run_detached_dispatch(
                    "test-agent", agent,
                    {"default_kill_grace_seconds": 2,
                     "max_stdout_bytes": 65536,
                     "max_stderr_bytes": 65536},
                    [sys.executable, self.echo_script, "hello"],
                    self.workspace, {"PATH": os.environ.get("PATH", "")},
                    None, run_dir, acl_warning, 30, time.monotonic())
            self.assertEqual(result["status"], "internal_error")
            self.assertEqual(exit_code, delegate.EXIT_INTERNAL)
            self.assertIn("simulated wmi outage", result["error"])
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


@unittest.skipUnless(_IS_WINDOWS, "detached dispatch requires WMI (Windows-only)")
class TestDetachedDispatch(DelegateTestBase):
    """End-to-end detached dispatch through real delegate.py subprocesses."""

    def _detached_config(self, script, **kw):
        a = make_agent(script, prompt_delivery="argument", **kw)
        a["allow_breakaway"] = True
        return self._config({"test-agent": a})

    def test_completed_echo_roundtrip(self):
        script = self._script("echo_arg", _ECHO_ARG)
        cfg = self._detached_config(script)
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertEqual(result["stdout"], "hello")
        self.assertEqual(result["child_exit_code"], 0)

    def test_exit_code_propagates_through_bootstrap(self):
        script = self._script("exit3", _EXIT_CODE)
        cfg = self._detached_config(script, extra_args=["3"])
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = self._assert_result(out, err, rc, "failed", 0)
        self.assertEqual(result["child_exit_code"], 3)

    def test_timeout_kills_detached_payload(self):
        """Detachment changes WHO owns the payload when the LAUNCHER dies;
        the delegate's own timeout kill must still work while it lives."""
        pid_file = os.path.join(self.tmpdir, "timeout-pid.txt")
        script = self._script("sleeper-to", _PID_SLEEPER)
        a = make_agent(script, prompt_delivery="argument")
        a["allow_breakaway"] = True
        a["default_timeout"] = 5
        a["minimum_timeout"] = 5
        cfg = self._config({"test-agent": a})
        out, err, rc = self._run("test-agent", task=pid_file, timeout=5,
                                 config=cfg, timeout_wrap=180)
        self._assert_result(out, err, rc, "timeout", 124)
        with open(pid_file) as f:
            payload_pid = int(f.read().strip())
        deadline = time.time() + 30
        while delegate.is_pid_alive(payload_pid) and time.time() < deadline:
            time.sleep(0.5)
        self.assertFalse(delegate.is_pid_alive(payload_pid),
                         "timeout kill must still reach a detached payload's tree")
```

2. Run, expect fail: `test_wmi_failure...` → `AttributeError: module 'delegate' has no attribute '_run_detached_dispatch'`; the three `TestDetachedDispatch` tests error on the missing attribute too (dispatch still goes down the contained path, so `test_completed_echo_roundtrip` may misleadingly pass against unmodified code — that is fine; Task 4's regression test is the one that cannot pass without this task).
```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_breakaway.py -v
```

3. Implement in `delegate.py`.

(a) Module level, directly after `wmi_spawn_detached` (Task 1):

```python
_DETACH_BOOTSTRAP = (
    "import json, subprocess, sys\n"
    "spec = json.load(open(sys.argv[1], 'r', encoding='utf-8'))\n"
    "with open(spec['stdout_log'], 'wb') as out, open(spec['stderr_log'], 'wb') as err:\n"
    "    rc = subprocess.call(spec['argv'], cwd=spec['cwd'], env=spec['env'],"
    " stdin=subprocess.DEVNULL, stdout=out, stderr=err)\n"
    "sys.exit(rc)\n"
)


def _read_log_capped(path, cap):
    """Read up to `cap` bytes of a run_dir log. Returns (text, truncated)."""
    try:
        with open(path, "rb") as f:
            data = f.read(cap + 1)
    except OSError:
        return "", False
    return data[:cap].decode("utf-8", errors="replace"), len(data) > cap


def _run_detached_dispatch(agent_name, agent, cfg, argv, workspace, child_env,
                           child_home, run_dir, acl_warning, timeout, start_time):
    """Launch the worker OUTSIDE the delegate's job via WMI (TOOL-032, #103).

    Custody contract: the payload is parented to the WMI provider service, so
    the runner killing this delegate (runner.py proc.kill) cannot collapse it
    through the KILL_ON_JOB_CLOSE cascade — surviving the launcher is the
    feature; outliving the budget if the delegate dies first is the documented
    cost (see the design comment at the BREAKAWAY_OK constant).

    Fail-loud: any WMI failure returns internal_error and NOTHING is launched;
    there is no fallback to a job-contained Popen, because silently containing
    a payload the operator configured as detached inverts the guarantee.

    The full child_env (isolated KIMI_CODE_HOME, _CHILD_MARKER, allowlisted
    vars) round-trips through detach_spec.json inside the ACL-hardened
    run_dir; the bootstrap re-creates it verbatim, so TOOL-013 home isolation
    and the anti-nesting marker survive detachment.

    RESIDUAL: log caps are enforced at read-back, not live — a detached
    payload can grow stdout.log/stderr.log past max_log_bytes while it runs.
    """
    spec_path = os.path.join(run_dir, "detach_spec.json")
    spec = {
        "argv": argv,
        "cwd": workspace,
        "env": child_env,
        "stdout_log": os.path.join(run_dir, "stdout.log"),
        "stderr_log": os.path.join(run_dir, "stderr.log"),
    }
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f)
    try:
        pid = wmi_spawn_detached(
            subprocess.list2cmdline([sys.executable, "-c", _DETACH_BOOTSTRAP, spec_path]),
            workspace)
        handle = open_waitable_process(pid)
    except CustodyError as e:
        return _make_result("internal_error", agent=agent_name, run_dir=run_dir,
                            acl_warning=acl_warning, child_home=child_home,
                            error=f"detached custody unavailable: {e}"), EXIT_INTERNAL

    deadline = time.monotonic() + timeout
    timed_out = False
    rc = None
    try:
        try:
            while True:
                if _interrupted.is_set():
                    break
                rc = reap_handle(handle, 100)
                if rc is not None:
                    break
                if time.monotonic() >= deadline:
                    # Final reap: the payload may have exited at the deadline.
                    rc = reap_handle(handle, 0)
                    if rc is not None:
                        break
                    timed_out = True
                    break
        except KeyboardInterrupt:
            global _interrupt_condition
            _interrupted.set()
            _interrupt_condition = "received KeyboardInterrupt"

        if _interrupted.is_set() or timed_out:
            # kill_process_tree with job=None: taskkill /T /F from the
            # bootstrap pid still reaches the payload tree through
            # parent-PID links while the bootstrap is alive.
            kill_process_tree(pid, cfg["default_kill_grace_seconds"], job=None)
            reap_handle(handle, 10000)
            stdout_text, stdout_trunc = _read_log_capped(
                spec["stdout_log"], cfg["max_stdout_bytes"])
            stderr_text, stderr_trunc = _read_log_capped(
                spec["stderr_log"], cfg["max_stderr_bytes"])
            duration = time.monotonic() - start_time
            status = "interrupted" if _interrupted.is_set() else "timeout"
            result = _make_result(
                status, agent=agent_name, duration=duration,
                stdout_text=stdout_text, stderr_text=stderr_text,
                stdout_trunc=stdout_trunc, stderr_trunc=stderr_trunc,
                run_dir=run_dir, acl_warning=acl_warning,
                child_session_id=extract_child_session_id(stdout_text, stderr_text),
                child_home=child_home,
                error=_interrupt_condition if _interrupted.is_set() else None,
            )
            return result, EXIT_INTERRUPTED if status == "interrupted" else EXIT_TIMEOUT

        duration = time.monotonic() - start_time
        stdout_text, stdout_trunc = _read_log_capped(
            spec["stdout_log"], cfg["max_stdout_bytes"])
        stderr_text, stderr_trunc = _read_log_capped(
            spec["stderr_log"], cfg["max_stderr_bytes"])
        status = "completed" if rc == 0 else "failed"
        result = _make_result(
            status, agent=agent_name, child_exit_code=rc, duration=duration,
            stdout_text=stdout_text, stderr_text=stderr_text,
            stdout_trunc=stdout_trunc, stderr_trunc=stderr_trunc,
            run_dir=run_dir, acl_warning=acl_warning,
            child_session_id=extract_child_session_id(stdout_text, stderr_text),
            child_home=child_home,
        )
        return result, EXIT_OK
    finally:
        close_process_handle(handle)
```

(b) In `run_delegate`, insert the branch immediately before the `# Launch — catch ValueError` comment (currently `:1170`), after the argv/task-file block (`:1159-1168`):

```python
    # TOOL-032 (#103): detached payloads launch via WMI outside the job.
    # The validator guarantees prompt_delivery is "argument" or "file" here,
    # so argv is complete and no stdin thread is needed.
    if agent.get("allow_breakaway", False):
        return _run_detached_dispatch(
            agent_name, agent, cfg, argv, workspace, child_env, child_home,
            run_dir, acl_warning, timeout, start_time)
```

Verify the names `agent_name`, `cfg`, `start_time` against the enclosing function while editing — they are the locals already used by the surrounding code (`run_delegate(args)`, `delegate.py:959`); do not invent new ones.

(c) Comment sweep — the old comments now misdescribe the semantics; update in place:
- `:95-107` (BREAKAWAY_OK comment): append: `# TOOL-032 (#103): for allow_breakaway agents the delegate now launches the worker via WMI (wmi_spawn_detached) entirely outside the job; this flag remains only as the composition primitive tested by test_breakaway.py.`
- `:172-176` (create_kill_on_close_job docstring comment): append: `# Detached-payload agents (allow_breakaway) no longer pass through here — see _run_detached_dispatch.`
- `:528-534` (validator comment): replace the sentence "Default False preserves the legacy everything-dies-with-the-worker guarantee." with `# Default False preserves the legacy everything-dies-with-the-worker guarantee. True = WMI-detached launch (TOOL-032): the payload is parented to the WMI provider and survives launcher death.`
- `:1190-1197` (Popen→assign residual comment): leave unchanged — it describes the contained path, which still exists.

4. Run, expect pass — all of `test_breakaway.py` green including Task 2's now-WMI-backed `test_true_accepted`:
```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_breakaway.py -v
python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
```
Also run the runner suite (envelope consumers) and model-dispatch parity, expect all green:
```
python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```

5. Commit (after confirmation):
```
git add harnesses/kimi-code/delegate/delegate.py harnesses/kimi-code/delegate/tests/test_breakaway.py
git commit -m "delegate: launch allow_breakaway workers via WMI outside the job (TOOL-032, #103)"
```

---

## Task 4 — Regression: payload survives launcher death (+ runner audit comment)

### Files
- Modify: `harnesses/kimi-code/runner/runner.py` (comment only)
- Test: `harnesses/kimi-code/delegate/tests/test_breakaway.py`

### Interfaces
Consumes (all previous tasks): the WMI dispatch path end-to-end; `delegate.is_pid_alive` (`:221-227`), `delegate._TASKKILL_EXE` (`:79`), `delegate._MINIMAL_TOOL_ENV` (`:82-87`) for test cleanup; the `_PID_SLEEPER` fixture (`test_delegate.py:193-201`, writes `os.getpid()` to `sys.argv[1]` then sleeps 300).
Produces: no new production symbols.

### Steps

1. Write the failing regression test. Append to `test_breakaway.py` before the `if __name__` block:

```python
@unittest.skipUnless(_IS_WINDOWS, "detached custody is Windows-only")
class TestDetachedCustodyRegression(DelegateTestBase):
    """#103 regression signature: the runner kills the delegate
    (runner.py:738,754 proc.kill), the delegate's job handle closes, and
    KILL_ON_JOB_CLOSE wipes the worker tree. A detached payload must not be
    in that job at all. The paired control proves the cascade still fires
    for contained workers, so the survival assertion is load-bearing."""

    def _popen_delegate(self, agent, config, task):
        argv = [sys.executable, str(_DELEGATE_DIR / "delegate.py"),
                "--agent", agent, "--workspace", self.workspace,
                "--task", task, "--timeout", "60"]
        env = dict(os.environ)
        env["DELEGATE_CONFIG"] = config
        return subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env)

    def _launch_then_kill_launcher(self, allow_breakaway):
        tag = "detached" if allow_breakaway else "contained"
        pid_file = os.path.join(self.tmpdir, f"pid-{tag}.txt")
        script = self._script(f"sleeper-{tag}", _PID_SLEEPER)
        a = make_agent(script, prompt_delivery="argument")
        a["allow_breakaway"] = allow_breakaway
        a["default_timeout"] = 300
        a["minimum_timeout"] = 5
        a["maximum_timeout"] = 300
        cfg = self._config({"test-agent": a})
        proc = self._popen_delegate("test-agent", cfg, pid_file)
        try:
            deadline = time.time() + 90  # WMI + PowerShell cold start can be slow
            while not os.path.exists(pid_file):
                if proc.poll() is not None:
                    out, err = proc.communicate()
                    self.fail(f"delegate exited early rc={proc.returncode}: "
                              f"{out.decode('utf-8', 'replace')!r} "
                              f"{err.decode('utf-8', 'replace')!r}")
                self.assertLess(time.time(), deadline,
                                f"{tag} payload never wrote its pid file")
                time.sleep(0.5)
            with open(pid_file) as f:
                payload_pid = int(f.read().strip())
            proc.kill()  # exactly what runner.run_delegate does on wrapper timeout
            proc.wait(timeout=30)
            return payload_pid
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)

    def _kill_payload(self, pid):
        subprocess.run([delegate._TASKKILL_EXE, "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=15,
                       env=delegate._MINIMAL_TOOL_ENV)

    def test_detached_payload_survives_launcher_death(self):
        payload_pid = self._launch_then_kill_launcher(allow_breakaway=True)
        try:
            time.sleep(2)  # let any job-close cascade land before asserting
            self.assertTrue(
                delegate.is_pid_alive(payload_pid),
                "detached payload died with its launcher — #103 regression")
        finally:
            self._kill_payload(payload_pid)

    def test_contained_payload_dies_with_launcher(self):
        """Control: without allow_breakaway the job-close cascade must still
        kill the worker — otherwise the survival test above proves nothing."""
        payload_pid = self._launch_then_kill_launcher(allow_breakaway=False)
        try:
            deadline = time.time() + 15
            while delegate.is_pid_alive(payload_pid) and time.time() < deadline:
                time.sleep(0.5)
            self.assertFalse(
                delegate.is_pid_alive(payload_pid),
                "contained payload survived launcher death — the "
                "kill-on-close guarantee is broken")
        finally:
            self._kill_payload(payload_pid)
```

Add `subprocess` to the test file's imports if not already present (the current file imports only `sys`, `unittest`, `pathlib.Path` plus the Task-1 additions; `subprocess` is NOT yet imported — add it).

2. Run, expect fail: `test_detached_payload_survives_launcher_death` fails its survival assertion against any build where the WMI dispatch path is absent or silently falls back to the contained Popen (the payload enters the job and dies with the killed delegate); the control `test_contained_payload_dies_with_launcher` passes both before and after. If Task 3 is complete, both pass immediately — in that case confirm the regression test is load-bearing by temporarily reverting the Task-3 branch insertion (comment out the `if agent.get("allow_breakaway", False):` block in `run_delegate`), re-running, and watching the survival test fail, then restoring. Record that observation in the commit message body.
```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -p test_breakaway.py -v
```

3. Runner audit comment (no behavior change). In `harnesses/kimi-code/runner/runner.py`, immediately above the `proc = subprocess.Popen(` at `:725`:

```python
        # No job object on the delegate, deliberately (#103): for contained
        # workers the delegate's own KILL_ON_JOB_CLOSE job must collapse when
        # this delegate dies — including via our proc.kill() below — and a
        # runner-side job would nest, not protect. For allow_breakaway
        # workers the payload is WMI-detached inside the delegate
        # (TOOL-032) and never enters any job this process could close.
```

4. Run the full delegate + runner suites, expect all green:
```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```

5. Commit (after confirmation):
```
git add harnesses/kimi-code/delegate/tests/test_breakaway.py harnesses/kimi-code/runner/runner.py
git commit -m "delegate: regression-test detached custody surviving launcher death (TOOL-032, #103)

Verified load-bearing: with the Task-3 WMI branch temporarily disabled,
test_detached_payload_survives_launcher_death fails (payload re-enters the
job and dies with the killed delegate); the contained control passes both
ways."
```

---

## Self-review pass

**Spec coverage** (ticket asks → owning task):
- "Audit all launch paths" → Spec section (audit performed at planning time, every spawn site enumerated with disposition) + runner comment codified in Task 4.
- "Make breakaway default+enforced for detached payloads" → Task 3: `allow_breakaway: true` now means a real WMI-detached launch, not a permissive-but-inert job flag; it is the default behavior of the flag (no second opt-in) and enforced (no contained fallback).
- "Fail loud when breakaway impossible" → Task 3 `except CustodyError → internal_error`, tested by `TestDetachedFailLoud`; non-Windows covered by `wmi_spawn_detached`'s platform raise.
- "WMI Win32_Process.Create escape (greenfield)" → Task 1 (`wmi_spawn_detached`, `open_waitable_process`, `reap_handle`, `_DETACH_BOOTSTRAP` in Task 3).
- "Regression test: launch marker payload, kill launcher, assert survival" → Task 4, plus the contained-control twin so the assertion is load-bearing.
- Branch `tool-032-detached-custody` → already checked out; no git-creation steps.

**Placeholder scan**: every step contains literal code, literal commands, literal commit messages; the only instruction to "verify names" in Task 3 step 3(b) points at specific existing locals in `run_delegate` (`agent_name`, `cfg`, `start_time`) — the executor confirms rather than invents. No TBDs.

**Type consistency across tasks**:
- `wmi_spawn_detached(command_line: str, working_dir: str, timeout_s: float=30) -> int` — Task 1 produces; Task 3 calls with `(str, str)`; Task 1 tests assert `int`.
- `open_waitable_process(pid: int) -> HANDLE`; `reap_handle(handle, timeout_ms: int) -> int | None` — Task 1 produces; Task 3's wait loop and tests use `int | None`; Task 1 test compares `== 0`.
- `CustodyError` — Task 1 raises; Task 3 catches only `CustodyError` (review-focus item 1 checks no broader swallow); Task 3 test mocks with `side_effect=delegate.CustodyError(...)`.
- `_run_detached_dispatch(...) -> (dict, int)` — Task 3 produces and its fail-loud test consumes in-process; the branch call site passes the same positional order as the signature.
- `_read_log_capped(path, cap) -> (str, bool)` — Task 3 internal; feeds `_make_result(stdout_text=str, stdout_trunc=bool)` matching the existing envelope types (`_ENVELOPE_KEYS`: `stdout` str, `stdout_truncated` bool).
- Config contract: Task 2 guarantees `prompt_delivery != "stdin"` for any agent reaching Task 3's branch — the branch relies on `argv` being complete and starts no stdin thread.

**Review-focus tests pinned to owning tasks**:
1. WMI outage → no explicit service-down test; mitigations pinned: `test_bad_powershell_raises_custody_error` (Task 1) + `test_wmi_failure_is_internal_error_and_launches_nothing` (Task 3). Residual flagged for review.
2. Spec-file confidentiality → documented residual in Task 3 docstring; owner: Task 3 review.
3. PID reuse → documented; `reap_handle` semantics asserted in Task 1 test.
4. Unbounded logs → documented residual in Task 3 docstring (`RESIDUAL:` line); owner: Task 3 review.
5. Post-launcher-death timeout → Task 4 cleans up its sleeper via `_kill_payload` in `finally`; cost statement lives in the Task 3 docstring and the pre-existing design comment.

**Known test-ordering note**: Task 2's modified `test_true_accepted` only exercises the WMI path once Task 3 lands; between Task 2 and Task 3 it passes via the legacy contained path. This is intentional (per-task green) and the Task 3 step-4 run is the first full-suite gate over the new path.

## Inventory corrections found during planning (verified against `mp_wt` @ 8982e24)

1. **Constant misnamed (inventory repeats it silently).** `delegate.py:111` defines `_PROCESS_QUERY_LIMIT_INFORMATION = 0x1000`, but `0x1000` is `PROCESS_QUERY_LIMITED_INFORMATION`; `PROCESS_QUERY_INFORMATION` is `0x0400`. Harmless today (used only for liveness checks). The plan reuses the existing constant for `open_waitable_process` rather than churning the rename; flag for a future cleanup ticket.
2. **Inventory is correct that `allow_breakaway` "shipped in b93bb4b" with the job-flag path, but the flag is weaker than the inventory implies.** `JOB_OBJECT_LIMIT_BREAKAWAY_OK` only *permits* a child to pass `CREATE_BREAKAWAY_FROM_JOB` to `CreateProcess`; Python's `subprocess.Popen` never sets that flag, so for Python workers the shipped escape is effectively inert — this is precisely why the WMI route is the enforcement mechanism rather than "more aggressive flag setting." The plan states this in Architecture; the inventory's §1a is otherwise accurate.
3. **All other checked references verified exact**: Popen at `delegate.py:1172-1180` (inventory said 1171-1180, off-by-one on the comment line only), job block `:1198-1218`, `create_kill_on_close_job :177-194`, `assign_process_to_job :196-212`, `is_pid_alive :221-227` (test-only, confirmed), validator `:528-534`, template guard `:444-447`, model force-off `:365`, `kill_process_tree :865-926` with exactly two callers, `_cleanup_handles :1386-1400`, `_make_result :1403-1427`, `runner.run_delegate :708` with Popen at `:723-726` and kills at `:738,:754`, `test_breakaway.py` 105 lines with `TestBreakawayValidation:27-56` and `TestBreakawayJobFlags:59-101`, `DelegateTestBase` at `test_delegate.py:349`, `make_agent` at `:262`, `run_delegate` test helper at `:279`, fixtures `_PID_SLEEPER:193` / `_ECHO_ARG:39` / `_EXIT_CODE:57`.
