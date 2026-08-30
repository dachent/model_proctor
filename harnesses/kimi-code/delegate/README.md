# delegate

Lean Windows subprocess wrapper that launches **one** configured external CLI worker with a bounded task, captures output safely, enforces a hard timeout with guaranteed process-tree kill, and emits exactly one JSON result envelope.

**Transport only:** no retries, no routing, no concurrency, no interpretation of child output.

## Requirements

- Windows 10/11
- Python 3.10+
- Standard library only (no third-party packages)
- Git Bash or cmd.exe for shell access

## Quick start

```
python delegate.py --agent example-coder --workspace C:/Dev/workspaces/myproject --task "Refactor utils.py" --timeout 600
```

Output is exactly one JSON object on stdout followed by a newline. Nothing else is printed on stdout.

## CLI interface

```
python delegate.py --agent <name> --workspace <path> (--task <text> | --task-file <path>) [--timeout <seconds>] [--resume-from <session_id>]
```

Exactly one of `--task` / `--task-file` is required. `--help` prints help text and exits 0 (no JSON envelope).

## Configuration

The wrapper loads `agents.json` from:

1. The path in the `DELEGATE_CONFIG` environment variable, if set.
2. `agents.json` next to `delegate.py`.

See `agents.example.json` for the full annotated schema. Key fields:

**Regenerating a live config (durability doctrine):** `agents.json` is deliberately untracked —
it contains machine-local absolute paths. It is disposable: to recreate it, copy
`agents.example.json` to `agents.json`, fill in the real CLI paths / `allowed_workspace_roots` /
agent roster for the machine, then run `python scripts/install.py` to install and ACL-harden it.
No other state is required.


| Field | Description |
|---|---|
| `allowed_workspace_roots` | List of root directories that workspaces must be contained within |
| `max_task_bytes` | Maximum UTF-8 encoded task size |
| `max_timeout_seconds` | Global timeout cap |
| `default_kill_grace_seconds` | Grace period between graceful and forceful kill (max 60) |
| `max_stdout_bytes` / `max_stderr_bytes` | Bounded tail sizes for in-memory output retention |
| `max_log_bytes` | Maximum bytes written to each disk log file (default 64 MiB). The pipe is still drained past this bound, but the log stops growing. |
| `agents` | Named agent definitions with command, prompt delivery, timeouts, env allowlist |

### Agent definition

| Field | Required | Description |
|---|---|---|
| `command` | Yes | Array: absolute path to executable + fixed args |
| `prompt_delivery` | Yes | `stdin`, `argument`, or `file` |
| `default_timeout` | Yes | Default timeout in seconds (must be ≤ `max_timeout_seconds`) |
| `minimum_timeout` | Yes | Minimum allowed timeout |
| `maximum_timeout` | Yes | Maximum allowed timeout |
| `environment_allowlist` | No | Env vars to inherit from wrapper environment |
| `environment` | No | Fixed non-secret env values to set |
| `required_environment` | No | Env vars that must be present before launch (fail closed) |
| `environment_passthrough` | No | Boolean — if true, child inherits full `os.environ` (for CLIs that require it; default false) |
| `write_allowed` | No | Boolean, **metadata only** — NOT an enforcement mechanism (default false) |
| `resume_args` | No | Array of args enabling session resume; exactly one element must contain the `{session_id}` placeholder (e.g. `["-r", "{session_id}"]`). Absent = the agent does not support resume |

> **`write_allowed` is metadata only.** The child runs with the caller's full token and filesystem permissions. This field exists for orchestration-layer policy decisions (e.g. "don't send write-flagged workers against read-only review tasks"). It does not constrain the child process.

## JSON result envelope

```json
{
  "schema_version": 1,
  "status": "completed|failed|timeout|invalid|internal_error|interrupted",
  "agent": "name",
  "child_exit_code": 0,
  "child_session_id": "session_9f3ab2c1-… or null",
  "child_home": "C:/.../delegate-kimi-home-… or null",
  "duration_seconds": 12.345,
  "stdout": "bounded tail",
  "stderr": "bounded tail",
  "stdout_truncated": false,
  "stderr_truncated": false,
  "stdout_log_truncated": false,
  "stderr_log_truncated": false,
  "run_dir": "C:/...",
  "acl_warning": false,
  "job_warning": false,
  "error": null
}
```

| Field | Meaning |
|---|---|
| `schema_version` | Envelope schema version (currently 1) |
| `stdout_log_truncated` / `stderr_log_truncated` | The disk log (`stdout.log` / `stderr.log` in `run_dir`) hit `max_log_bytes` and stopped growing. Any extraction or verdict drawn from a truncated log is void until the evidence is re-acquired — raise `max_log_bytes` for log-heavy agents |
| `child_session_id` | Session id scraped from the child's output tails (kimi `To resume this session:` footer), or `null` when none was printed. Feed it back via `--resume-from` |
| `child_home` | The per-dispatch isolated `KIMI_CODE_HOME` (see "KIMI_CODE_HOME isolation"), or `null` when isolation is disabled. Caller-owned: meter from `<child_home>/sessions/`, then delete |
| `acl_warning` | `icacls` hardening of the run directory failed (run continued; logs may inherit default ACLs) |
| `job_warning` | Job Object creation/assignment failed; timeout falls back to `taskkill`-only tree kill (degraded — grandchildren created in the Popen→assign window may escape) |

| Status | Meaning |
|---|---|
| `completed` | Child exited 0 |
| `failed` | Child started, exited nonzero (`child_exit_code` preserved, wrapper exits 0) |
| `timeout` | Deadline expired and process tree terminated (`child_exit_code` null) |
| `invalid` | Input/config invalid, no child launched |
| `internal_error` | Wrapper failure independent of child (error field = exception class name only) |
| `interrupted` | Wrapper received a termination signal |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Valid JSON emitted for completed **or** ordinary child failure |
| 64 | Invalid input or configuration |
| 70 | Internal wrapper error |
| 124 | Timeout |
| 130 | Interrupted by SIGINT |

> Both `completed` and `failed` exit 0 by design. The JSON envelope is authoritative; `child_exit_code` carries the child's result. This prevents arbitrary child exit codes from colliding with wrapper-control codes.

> **Note:** Exit code 143 (POSIX SIGTERM) is intentionally omitted. Windows has no POSIX SIGTERM signal.

> **Wall-clock ceiling:** the actual wall-clock upper bound is approximately `timeout + default_kill_grace_seconds + overhead` (taskkill invocations, `proc.wait`, reader joins) ≈ `timeout + grace + ~30 s`, not `timeout`.

## Session resume

Headless `kimi -p` runs create a server-side session and print
`To resume this session: kimi -r session_<id>` on stderr. Resuming that session
with `kimi -r <session_id> -p "<followup>"` reuses the prompt cache instead of
paying a fresh cold dispatch — measured ~5× cheaper input tokens on resume
(56K cached vs 56K uncached input tokens). Retry/follow-up steps in an
orchestration cascade should therefore resume the same worker session rather
than launch a cold one.

Configuration is per agent via `resume_args` — an argv template containing
exactly one `{session_id}` placeholder:

```json
"resume_args": ["-r", "{session_id}"]
```

Usage: take `child_session_id` from a run's envelope and pass it back:

```
python delegate.py --agent glm-flash-worker --workspace C:/Dev/ws --task "Add a test" 
python delegate.py --agent glm-flash-worker --workspace C:/Dev/ws --resume-from session_9f3ab2c1 --task "Now cover the edge case"
```

The substituted args are inserted into argv immediately after the executable
(`[command[0]] + resume_args + command[1:]`). Session ids are validated
(nonempty, no NUL, `^[A-Za-z0-9_-]+$` only — they land on a command line).
Passing `--resume-from` for an agent without `resume_args` is `invalid`
(exit 64) — fail closed rather than silently cold-starting.

The wrapper scrapes the bounded stdout/stderr tails for
`kimi -r (session_[A-Za-z0-9-]+)` and reports the match as
`child_session_id` in the envelope (`null` when absent). Only kimi-style
footers are recognized; agents whose CLIs use different resume mechanics
should not declare `resume_args` until verified.

## Security model

### Workspace containment

Workspaces are validated against `allowed_workspace_roots` using `Path.resolve(strict=True)` and separator-aware comparison (`os.path.normcase()` on both sides). A sibling directory like `C:\Projects_evil` will **not** pass for root `C:\Projects`. Drive roots (`C:\`) work correctly. UNC paths (`\\server\share`) are rejected unless the matched root is itself UNC. `\\?\` and `\\?\UNC\` extended-length prefixes are stripped/handled before comparison.

Symlink and junction escapes are prevented by `resolve(strict=True)` which follows symlinks and junctions to their real targets before the containment check.

### Environment isolation

The child process does **not** inherit `os.environ.copy()` by default. The child environment is constructed from:

1. A fixed Windows base allowlist (always included when present): `SystemRoot`, `SystemDrive`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `TEMP`, `TMP`, `PATH`, `PATHEXT`, `NUMBER_OF_PROCESSORS`, `PROCESSOR_ARCHITECTURE`, plus standard non-secret system vars (`windir`, `OS`, `PUBLIC`, `PROGRAMDATA`, `HOMEDRIVE`, `HOMEPATH`, `ProgramFiles*`, `CommonProgramFiles*`).
2. The agent's `environment_allowlist` entries that are present in the wrapper's environment.
3. The agent's fixed `environment` map (non-secret values only).

**`environment_passthrough: true`** (per-agent, default `false`) is an explicit opt-out: the child inherits the full user environment. Use only for trusted CLIs whose launchers genuinely require it.

Environment values are never logged or serialized. Environment names are validated (nonempty, no `=`, no NUL, no case-fold collisions). API keys should be provided through `required_environment`: the wrapper checks presence before launch and fails closed without printing the value.

### Tool resolution

`taskkill.exe` and `icacls.exe` are resolved from `%SystemRoot%\System32` as absolute paths and invoked with a minimal explicit environment. This prevents PATH/cwd hijack of the wrapper's own kill and ACL-hardening tools.

### No shell invocation

`shell=False` is always used. Task text delivered as a command argument is treated as literal text, never as a shell command. A task containing `& echo pwned` is passed verbatim to the child, not executed.

> **Argument delivery note:** when `prompt_delivery` is `"argument"`, the task text is appended as the final argv element. Configured commands must make the task a flag **value** (e.g. `["cli.exe", "--prompt"]` so the task becomes the value of `--prompt`). Bare trailing-argument configs (where the task is a positional argument) are unsupported — a task starting with `-` or `/` would be parsed as an option by the child CLI. All live configs use a flag-value pattern.

### Run directory

Each invocation creates a temporary run directory (`delegate-*` under the system temp root). On Windows, ACLs are hardened via `icacls /inheritance:r /grant:r "<USERDOMAIN>\<user>:(OI)(CI)F"`, removing inherited permissions and granting full control only to the current user. If `icacls` fails (nonzero exit code or exception), an `acl_warning` flag is set but the run continues.

Only child stdout/stderr are written to the run directory (`stdout.log`, `stderr.log`). Disk log size is bounded by `max_log_bytes` (default 64 MiB); the pipe is still drained past this bound to prevent deadlock, but the log file stops growing. The wrapper never writes task text, command arrays, config, or environment names/values to disk.

### DLL search residual risk

The wrapper does **not** call `SetDllDirectoryW`. That API is per-process and not inherited by child processes, so it does not affect the worker's DLL search path. The child process runs with `cwd=workspace`, which is a user-controlled directory. A planted DLL in the workspace could be loaded by the child process. This is a residual risk that cannot be mitigated from the wrapper without controlling the child's DLL search behavior. Operators should ensure workspace directories are not writable by untrusted parties.

## Windows process-tree kill design

### Why not `proc.terminate()`?

`proc.terminate()` kills only the root process. On Windows, this orphans all child and grandchild processes — they continue running indefinitely. This was the critical finding that motivated the Job Object design.

### Job Object as primary tree-kill mechanism

The wrapper creates a Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (without `JOB_OBJECT_LIMIT_BREAKAWAY_OK`, so children cannot escape the job). When the job handle is closed, the kernel atomically terminates all processes assigned to the job, including their descendants.

All Win32 calls use `ctypes.WinDLL("kernel32", use_last_error=True)` with declared `argtypes`/`restype` (pointer-sized `HANDLE`), so `ctypes.get_last_error()` returns reliable error codes.

### Kill sequence

1. `taskkill /PID <pid> /T` — graceful tree termination (no `/F`), sends `WM_CLOSE`. Console processes without a message loop ignore it, but it is cheap and non-destructive.
2. Wait `default_kill_grace_seconds` — the child may exit during this window.
3. `taskkill /PID <pid> /T /F` — **force** tree kill while parent-PID links are intact. This catches descendants created in the Popen→assign window that are not in the job. `/T` enumerates descendants by walking parent-PID links from the root, which requires the root to still be alive.
4. Close the Job Object handle — kernel terminates anything still in the job.
5. Belt-and-braces `taskkill /T /F` again for any process that survived both prior steps.
6. `proc.wait()` — reap the child.
7. Join reader threads.

The force kill (step 3) runs **before** job close (step 4) so that out-of-job descendants are still reachable via parent-PID links. If the job were closed first, the root would be terminated and `taskkill /T` could not enumerate the tree.

### Popen→assign residual window

There is a small window between `Popen` returning and `AssignProcessToJobObject` completing where the child can spawn descendants that never enter the job. Python's stdlib `subprocess` has no `CREATE_SUSPENDED` equivalent, so this window cannot be fully closed. The mitigation is the kill-sequence ordering above: force `taskkill /T /F` runs while the root is alive, so out-of-job descendants are still reachable. If `job_warning` is `true`, the job was not successfully created and tree kill relies entirely on `taskkill /T /F`.

### `CREATE_NEW_PROCESS_GROUP` is deliberately NOT used

It is a console-signal grouping mechanism, not a kill boundary. It does not help with process-tree termination and can interfere with console signal delivery.

## Log content caveat

**Child output is captured verbatim and written to `stdout.log` and `stderr.log` in the run directory.** The child may itself emit task excerpts, source code, credentials, or other sensitive information. The wrapper does not redact, filter, or interpret child output.

Logs persist in the run directory until manually deleted. The `run_dir` field in the JSON result provides the path for inspection. The run directory should be deleted only after the caller confirms the evidence has been archived (e.g. copied into a durable evidence store) — not merely after a successful inspection.

## Child CLI side effects

Each CLI worker may keep its own state outside the wrapper's control. In particular, every headless `kimi -p` invocation creates a resumable session under the user's Kimi state directory and prints a `To resume this session:` footer on stderr (surfaced as `child_session_id` in the envelope; see "Session resume"). Worker sessions accumulate over time; prune them periodically if worker volume is high. The wrapper does not manage or delete child CLI sessions.

### KIMI_CODE_HOME isolation (TOOL-013)

Kimi Code CLI auto-registers every CWD it runs in as a workspace under the active `KIMI_CODE_HOME` — without isolation, every dispatched task pollutes the user's real `~/.kimi-code` (phantom workspaces, session-index bloat; observed 2026-08-25 after ~130 pilot dispatches).

To prevent that, every dispatch gets a **per-dispatch isolated, seeded home**: `delegate` creates `delegate-kimi-home-*` in the temp dir, seeds it with `config.toml`, `device_id`, `region`, and `credentials/` from the operator's home (an empty home fails auth — verified), and injects it into the child environment directly (parent-injected invariants do not go through the agent's environment allowlist). The envelope's `child_home` field reports the path.

**Lifecycle:** the caller owns the home after the run. Wire logs for cost metering live under `<child_home>/sessions/`, so `delegate` never deletes it — callers (e.g. `runner/pilot.py`) meter, then delete, and sweep orphaned `delegate-kimi-home-*` dirs. Set `DELEGATE_NO_HOME_ISOLATION=1` to disable (required for `--resume-from`, which needs the prior session's home).

## Tests

```
python -m unittest discover -s delegate/tests -v
```

Tests use temporary directories and tiny Python fixture scripts invoked with `sys.executable` — no third-party CLIs are needed.
