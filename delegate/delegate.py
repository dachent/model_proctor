#!/usr/bin/env python3
"""delegate — lean Windows subprocess wrapper for bounded external CLI workers.

Launches ONE configured CLI as a subprocess with a bounded task, captures output
safely, enforces a hard timeout with guaranteed process-tree kill via Windows Job
Objects, and emits exactly one JSON result envelope on stdout.

Transport only: no retries, no routing, no concurrency, no interpretation of
child output.

Exit codes: 0 (completed/failed), 64 (invalid), 70 (internal_error),
124 (timeout), 130 (interrupted).  Both ``completed`` and ``failed`` exit 0
by design — the JSON envelope is authoritative; ``child_exit_code`` carries
the child's result.  143 (POSIX SIGTERM) is intentionally omitted because
Windows has no POSIX SIGTERM.

Actual wall-clock ceiling ≈ timeout + default_kill_grace_seconds + overhead
(taskkill invocations, proc.wait, reader joins) ≈ timeout + grace + ~30 s.
"""

import argparse
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Platform guard
# ---------------------------------------------------------------------------

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_INVALID = 64
EXIT_INTERNAL = 70
EXIT_TIMEOUT = 124
EXIT_INTERRUPTED = 130

# ---------------------------------------------------------------------------
# Windows base environment allowlist
# ---------------------------------------------------------------------------

_WINDOWS_BASE_ALLOWLIST = frozenset({
    "SystemRoot", "SystemDrive", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "TEMP", "TMP", "PATH", "PATHEXT", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    # Standard non-secret system vars several Windows CLIs require (grok's
    # launcher 401s without some of these; found by env bisection).
    "windir", "OS", "PUBLIC", "PROGRAMDATA", "HOMEDRIVE", "HOMEPATH",
    "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
    "CommonProgramFiles", "CommonProgramFiles(x86)", "CommonProgramW6432",
})

# ---------------------------------------------------------------------------
# Resolved absolute paths for taskkill / icacls (avoid PATH/cwd hijack)
# ---------------------------------------------------------------------------

def _resolve_system_tool(name):
    """Resolve a System32 tool to an absolute path."""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(root, "System32", name)

_TASKKILL_EXE = _resolve_system_tool("taskkill.exe") if _IS_WINDOWS else "taskkill"
_ICACLS_EXE = _resolve_system_tool("icacls.exe") if _IS_WINDOWS else "icacls"

_MINIMAL_TOOL_ENV = None
if _IS_WINDOWS:
    _MINIMAL_TOOL_ENV = {}
    for _k in ("SystemRoot", "SystemDrive", "PATH", "PATHEXT", "TEMP", "TMP"):
        if _k in os.environ:
            _MINIMAL_TOOL_ENV[_k] = os.environ[_k]

# ---------------------------------------------------------------------------
# Job Object ctypes structures, constants, and typed kernel32 (Windows only)
# ---------------------------------------------------------------------------

if _IS_WINDOWS:
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    # Opt-in per agent (`allow_breakaway`): lets descendants escape the job via
    # CREATE_BREAKAWAY_FROM_JOB so deliberately-detached pipelines survive
    # worker exit.
    #
    # Timeout kills remain effective for descendants still reachable through
    # parent-PID links, because kill_process_tree force-taskkill /T /F's them
    # before closing the job. RESIDUAL: that reachability is exactly what
    # breakaway is bought to remove. A process that escaped the job AND whose
    # intermediate parent has already exited is in neither the job nor the /T
    # walk, so a timeout kill will not reach it — surviving the run is the
    # feature, and outliving a timeout is its cost. Enable per agent only
    # where an orphaned pipeline is preferable to a killed one.
    _JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x0400
    _JobObjectExtendedLimitInformation = 9
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_LIMIT_INFORMATION = 0x1000

    # Use WinDLL with use_last_error=True so ctypes.get_last_error() works.
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Declare argtypes/restype for every Win32 call — default c_int truncates
    # 64-bit HANDLEs and makes get_last_error unreliable.
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.CreateJobObjectW.restype = wintypes.HANDLE

    _k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    _k32.SetInformationJobObject.restype = wintypes.BOOL

    _k32.OpenProcess.argtypes = [
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
    ]
    _k32.OpenProcess.restype = wintypes.HANDLE

    _k32.AssignProcessToJobObject.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE,
    ]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL

    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_void_p),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    # Breakaway is opt-in per agent config (`allow_breakaway`, default False):
    # with the flag unset, children calling CreateProcess(CREATE_BREAKAWAY_FROM_JOB)
    # fail, keeping all descendants inside the kill-on-close boundary (legacy
    # guarantee). With the flag set, detached grandchildren survive worker exit —
    # required for workers that launch supervised long-running pipelines.
    def create_kill_on_close_job(allow_breakaway=False):
        """Create a Job Object that kills all assigned processes when the handle closes."""
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if allow_breakaway:
            info.BasicLimitInformation.LimitFlags |= _JOB_OBJECT_LIMIT_BREAKAWAY_OK
        ok = _k32.SetInformationJobObject(
            job, _JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        if not ok:
            err = ctypes.get_last_error()
            _k32.CloseHandle(job)
            raise ctypes.WinError(err)
        return job

    def assign_process_to_job(job, pid):
        """Open process by PID and assign to the Job Object. Returns the process handle."""
        handle = _k32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid,
        )
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if not _k32.AssignProcessToJobObject(job, handle):
            err = ctypes.get_last_error()
            # ERROR_ACCESS_DENIED (5): the process may have already exited, or
            # it is already in an incompatible job. Keep the handle — closing
            # it is still correct, and the job handle close will terminate
            # any descendants that did enter the job.
            if err != 5:
                _k32.CloseHandle(handle)
                raise ctypes.WinError(err)
        return handle

    def close_job(job):
        """Close the job handle. The kernel then terminates all processes in the job."""
        _k32.CloseHandle(job)

    def close_process_handle(handle):
        _k32.CloseHandle(handle)

    def is_pid_alive(pid):
        """Check if a process with the given PID is still running."""
        handle = _k32.OpenProcess(_PROCESS_QUERY_LIMIT_INFORMATION, False, pid)
        if not handle:
            return False
        _k32.CloseHandle(handle)
        return True


# ---------------------------------------------------------------------------
# Configuration loading and validation
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Raised when configuration is invalid."""


# Placeholder substituted with the session id inside an agent's resume_args.
_RESUME_PLACEHOLDER = "{session_id}"


def _resolve_config_path():
    """Resolve the config path from DELEGATE_CONFIG env var or agents.json next to this file."""
    env_path = os.environ.get("DELEGATE_CONFIG")
    if env_path:
        if "\x00" in env_path:
            raise ConfigError("NUL byte in DELEGATE_CONFIG path")
        return Path(env_path)
    return Path(__file__).resolve().parent / "agents.json"


def _check_nul(value, name):
    """Reject NUL bytes in a string value."""
    if "\x00" in value:
        raise ConfigError(f"NUL byte present in {name}")


def _check_nul_list(values, name):
    for i, v in enumerate(values):
        if "\x00" in v:
            raise ConfigError(f"NUL byte present in {name}[{i}]")


def _validate_finite_number(val, name):
    """Require a finite, non-NaN, non-bool number."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ConfigError(f"{name} must be a number")
    if not math.isfinite(val):
        raise ConfigError(f"{name} must be finite")


def _validate_env_name(name, agent_label):
    """Validate a single environment variable name."""
    if not name:
        raise ConfigError(f"Agent '{agent_label}': environment name must not be empty")
    if "=" in name:
        raise ConfigError(f"Agent '{agent_label}': environment name must not contain '='")
    _check_nul(name, f"agent '{agent_label}' env name")


def load_config():
    """Load and validate agents.json. Returns the parsed config dict. Raises ConfigError on any issue."""
    path = _resolve_config_path()
    try:
        if not path.exists():
            raise ConfigError(f"Configuration file not found: {path}")
    except (OSError, ValueError) as e:
        raise ConfigError(f"Cannot resolve configuration path: {e}")
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ConfigError(f"Cannot read configuration file: {e}")
    try:
        cfg = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"Configuration is not valid JSON: {e}")
    _validate_config(cfg, path)
    return cfg


def _validate_config(cfg, cfg_path):
    if not isinstance(cfg, dict):
        raise ConfigError("Configuration root must be an object")
    for field, typ in [
        ("allowed_workspace_roots", list),
        ("max_task_bytes", int),
        ("max_timeout_seconds", (int, float)),
        ("default_kill_grace_seconds", (int, float)),
        ("max_stdout_bytes", int),
        ("max_stderr_bytes", int),
        ("agents", dict),
    ]:
        if field not in cfg:
            raise ConfigError(f"Missing required field: {field}")
        if not isinstance(cfg[field], typ) or isinstance(cfg[field], bool):
            raise ConfigError(f"Field {field} must be of type {typ.__name__ if hasattr(typ, '__name__') else typ}")
    # Optional max_log_bytes
    if "max_log_bytes" in cfg:
        if not isinstance(cfg["max_log_bytes"], int) or isinstance(cfg["max_log_bytes"], bool):
            raise ConfigError("max_log_bytes must be an integer")
        if cfg["max_log_bytes"] <= 0:
            raise ConfigError("max_log_bytes must be positive")
    if cfg["max_task_bytes"] <= 0:
        raise ConfigError("max_task_bytes must be positive")
    _validate_finite_number(cfg["max_timeout_seconds"], "max_timeout_seconds")
    if cfg["max_timeout_seconds"] <= 0:
        raise ConfigError("max_timeout_seconds must be positive")
    _validate_finite_number(cfg["default_kill_grace_seconds"], "default_kill_grace_seconds")
    if cfg["default_kill_grace_seconds"] < 0:
        raise ConfigError("default_kill_grace_seconds must be non-negative")
    if cfg["default_kill_grace_seconds"] > 60:
        raise ConfigError("default_kill_grace_seconds must not exceed 60")
    if cfg["max_stdout_bytes"] <= 0:
        raise ConfigError("max_stdout_bytes must be positive")
    if cfg["max_stderr_bytes"] <= 0:
        raise ConfigError("max_stderr_bytes must be positive")
    if not cfg["allowed_workspace_roots"]:
        raise ConfigError("allowed_workspace_roots must not be empty")
    for root in cfg["allowed_workspace_roots"]:
        if not isinstance(root, str):
            raise ConfigError("allowed_workspace_roots entries must be strings")
    if not cfg["agents"]:
        raise ConfigError("agents must not be empty")
    for name, agent in cfg["agents"].items():
        _validate_agent(name, agent, cfg["max_timeout_seconds"])


def _validate_agent(name, agent, global_max_timeout, check_executable=False):
    if not isinstance(agent, dict):
        raise ConfigError(f"Agent '{name}' must be an object")
    # command
    if "command" not in agent or not isinstance(agent["command"], list) or not agent["command"]:
        raise ConfigError(f"Agent '{name}': command must be a non-empty array")
    for arg in agent["command"]:
        if not isinstance(arg, str):
            raise ConfigError(f"Agent '{name}': command elements must be strings")
    _check_nul_list(agent["command"], f"agent '{name}' command")
    exe = agent["command"][0]
    if not os.path.isabs(exe):
        raise ConfigError(f"Agent '{name}': executable must be an absolute path")
    # Executable existence is checked lazily (check_executable=True) only for the
    # invoked agent: an unrelated agent whose CLI was uninstalled must not block runs.
    if check_executable:
        exe_path = Path(exe)
        if not exe_path.exists():
            raise ConfigError(f"Agent '{name}': executable does not exist: {exe}")
        if not exe_path.is_file():
            raise ConfigError(f"Agent '{name}': executable is not a regular file: {exe}")
    # prompt_delivery
    pd = agent.get("prompt_delivery")
    if pd not in ("stdin", "argument", "file"):
        raise ConfigError(f"Agent '{name}': prompt_delivery must be 'stdin', 'argument', or 'file'")
    # timeouts
    for field in ("default_timeout", "minimum_timeout", "maximum_timeout"):
        if field not in agent:
            raise ConfigError(f"Agent '{name}': missing {field}")
        _validate_finite_number(agent[field], f"Agent '{name}': {field}")
        if agent[field] <= 0:
            raise ConfigError(f"Agent '{name}': {field} must be positive")
    if agent["minimum_timeout"] > agent["maximum_timeout"]:
        raise ConfigError(f"Agent '{name}': minimum_timeout exceeds maximum_timeout")
    if not (agent["minimum_timeout"] <= agent["default_timeout"] <= agent["maximum_timeout"]):
        raise ConfigError(f"Agent '{name}': default_timeout must be within [minimum_timeout, maximum_timeout]")
    if agent["default_timeout"] > global_max_timeout:
        raise ConfigError(f"Agent '{name}': default_timeout exceeds global max_timeout_seconds")
    # environment_allowlist
    ea = agent.get("environment_allowlist", [])
    if not isinstance(ea, list):
        raise ConfigError(f"Agent '{name}': environment_allowlist must be an array")
    for v in ea:
        if not isinstance(v, str):
            raise ConfigError(f"Agent '{name}': environment_allowlist entries must be strings")
        _validate_env_name(v, name)
    # environment — fixed values
    env = agent.get("environment", {})
    if not isinstance(env, dict):
        raise ConfigError(f"Agent '{name}': environment must be an object")
    seen = set()
    for k, v in env.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ConfigError(f"Agent '{name}': environment keys and values must be strings")
        _validate_env_name(k, name)
        _check_nul(v, f"agent '{name}' environment value '{k}'")
        lc = k.lower()
        if lc in seen:
            raise ConfigError(f"Agent '{name}': case-fold collision in environment key '{k}'")
        seen.add(lc)
    # required_environment
    re_env = agent.get("required_environment", [])
    if not isinstance(re_env, list):
        raise ConfigError(f"Agent '{name}': required_environment must be an array")
    for v in re_env:
        if not isinstance(v, str):
            raise ConfigError(f"Agent '{name}': required_environment entries must be strings")
        _validate_env_name(v, name)
    # environment_passthrough
    ep = agent.get("environment_passthrough", False)
    if not isinstance(ep, bool):
        raise ConfigError(f"Agent '{name}': environment_passthrough must be a boolean")
    # write_allowed — metadata only, NOT an enforcement mechanism.  The child
    # runs with the caller's full token and filesystem permissions.  This field
    # is present for orchestration-layer policy decisions, not for confinement.
    wa = agent.get("write_allowed", False)
    if not isinstance(wa, bool):
        raise ConfigError(f"Agent '{name}': write_allowed must be a boolean")
    # allow_breakaway — opt-in JOB_OBJECT_LIMIT_BREAKAWAY_OK for workers that
    # must launch deliberately-detached long-running processes (e.g. weekly
    # pipeline orchestrators). Default False preserves the legacy
    # everything-dies-with-the-worker guarantee.
    ab = agent.get("allow_breakaway", False)
    if not isinstance(ab, bool):
        raise ConfigError(f"Agent '{name}': allow_breakaway must be a boolean")
    # resume_args — optional argv template for session resume (e.g. kimi's
    # ["-r", "{session_id}"]).  Exactly one element must contain the
    # placeholder.  Missing field = the agent does not support resume.
    ra = agent.get("resume_args")
    if ra is not None:
        if not isinstance(ra, list):
            raise ConfigError(f"Agent '{name}': resume_args must be an array")
        for v in ra:
            if not isinstance(v, str):
                raise ConfigError(f"Agent '{name}': resume_args entries must be strings")
        _check_nul_list(ra, f"agent '{name}' resume_args")
        placeholder_count = sum(v.count(_RESUME_PLACEHOLDER) for v in ra)
        if placeholder_count != 1:
            raise ConfigError(
                f"Agent '{name}': resume_args must contain exactly one "
                f"'{_RESUME_PLACEHOLDER}' placeholder")


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class InputError(Exception):
    """Raised when invocation input is invalid."""


def _strip_extended_prefix(path_str):
    r"""Strip \\?\ extended-length prefix, converting \\?\UNC\ to \\."""
    if path_str.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path_str[7:]
    if path_str.startswith("\\\\?\\"):
        return path_str[4:]
    return path_str


def _is_unc(path_str):
    """Check if a path is a UNC path (starts with \\\\)."""
    stripped = _strip_extended_prefix(path_str)
    return stripped.startswith("\\\\")


def _workspace_contained(ws_resolved, root_resolved):
    """Check if workspace is contained within an allowed root.

    Uses normcase on both sides and separator-aware prefix comparison.
    Rstrips trailing separators from the root so ``C:\\`` matches ``C:\\sub``.
    """
    ws_norm = os.path.normcase(ws_resolved)
    root_norm = os.path.normcase(root_resolved).rstrip(os.sep)
    if ws_norm == root_norm:
        return True
    return ws_norm.startswith(root_norm + os.sep)


def validate_workspace(workspace_arg, allowed_roots):
    """Resolve and validate the workspace path against allowed roots. Returns the resolved path string."""
    if "\x00" in workspace_arg:
        raise InputError("NUL byte in workspace path")
    try:
        ws = Path(workspace_arg).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as e:
        raise InputError(f"Workspace does not exist: {e}")
    if not ws.is_dir():
        raise InputError("Workspace is not a directory")
    ws_str = str(ws)
    ws_is_unc = _is_unc(ws_str)
    matched = False
    for root_arg in allowed_roots:
        try:
            root = Path(root_arg).resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            continue
        root_str = str(root)
        root_is_unc = _is_unc(root_str)
        if ws_is_unc and not root_is_unc:
            continue
        if _workspace_contained(ws_str, root_str):
            matched = True
            break
    if not matched:
        raise InputError("Workspace is not within any allowed workspace root")
    return ws_str


def validate_task(task_text, max_task_bytes):
    """Validate task text: nonempty, no NUL, within byte limit. Returns the validated task string."""
    if "\x00" in task_text:
        raise InputError("NUL byte in task text")
    if not task_text:
        raise InputError("Task is empty")
    encoded = task_text.encode("utf-8")
    if len(encoded) > max_task_bytes:
        raise InputError("Task exceeds maximum allowed size")
    return task_text


# Session ids land on a command line, so the charset is deliberately tight:
# alphanumerics, underscore, hyphen only. Kimi session ids match this.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_session_id(session_id):
    """Validate a --resume-from session id: nonempty, no NUL, safe charset."""
    if "\x00" in session_id:
        raise InputError("NUL byte in session id")
    if not session_id:
        raise InputError("Session id is empty")
    if not _SESSION_ID_RE.match(session_id):
        raise InputError("Session id contains invalid characters")
    return session_id


# Kimi headless runs print "To resume this session: kimi -r session_<id>".
_CHILD_SESSION_RE = re.compile(r"kimi -r (session_[A-Za-z0-9-]+)")


def extract_child_session_id(stdout_text, stderr_text):
    """Scan the bounded output tails for the child's resume hint.

    Returns the session id string, or None if no hint was found. Stderr is
    scanned first — kimi prints the resume footer there.
    """
    for text in (stderr_text, stdout_text):
        m = _CHILD_SESSION_RE.search(text)
        if m:
            return m.group(1)
    return None


def validate_timeout(timeout_arg, agent, max_timeout_seconds):
    """Validate and clamp the timeout value. Returns the resolved timeout in seconds."""
    if timeout_arg is None:
        val = float(agent["default_timeout"])
    else:
        try:
            val = float(timeout_arg)
        except (ValueError, TypeError):
            raise InputError("Timeout is not a valid number")
    if not math.isfinite(val) or val <= 0:
        raise InputError("Timeout must be a finite positive number")
    min_t = float(agent["minimum_timeout"])
    max_t = float(agent["maximum_timeout"])
    if val < min_t:
        raise InputError(f"Timeout below agent minimum ({min_t})")
    if val > max_t:
        raise InputError(f"Timeout above agent maximum ({max_t})")
    if val > float(max_timeout_seconds):
        raise InputError(f"Timeout above global maximum ({max_timeout_seconds})")
    return val


def check_required_environment(required_env):
    """Check that all required environment variables are present. Never prints values."""
    missing = []
    for var in required_env:
        if var not in os.environ:
            missing.append(var)
    if missing:
        raise InputError(f"Missing required environment variable(s): {', '.join(missing)}")


def build_child_environment(agent):
    """Construct the child process environment. Never logs values.

    Default: constructed from the Windows base allowlist + the agent's allowlist
    + fixed values. If the agent sets environment_passthrough: true (explicit
    opt-in for trusted CLIs whose launchers require the full user environment),
    the child inherits os.environ wholesale plus fixed values.
    """
    if agent.get("environment_passthrough", False):
        child_env = dict(os.environ)
        for k, v in agent.get("environment", {}).items():
            child_env[k] = v
        return child_env
    child_env = {}
    for var in _WINDOWS_BASE_ALLOWLIST:
        if var in os.environ:
            child_env[var] = os.environ[var]
    for var in agent.get("environment_allowlist", []):
        if var in os.environ:
            child_env[var] = os.environ[var]
    for k, v in agent.get("environment", {}).items():
        child_env[k] = v
    return child_env


# ---------------------------------------------------------------------------
# Run directory and ACL hardening
# ---------------------------------------------------------------------------

def create_run_dir():
    """Create a temporary run directory and harden ACLs. Returns (run_dir_path, acl_warning_bool)."""
    run_dir = tempfile.mkdtemp(prefix="delegate-")
    acl_warning = False
    if _IS_WINDOWS:
        try:
            user = os.getlogin()
            domain = os.environ.get("USERDOMAIN", "")
            principal = f"{domain}\\{user}" if domain else user
            result = subprocess.run(
                [_ICACLS_EXE, run_dir, "/inheritance:r",
                 "/grant:r", f"{principal}:(OI)(CI)F"],
                capture_output=True, timeout=15, shell=False,
                env=_MINIMAL_TOOL_ENV,
            )
            if result.returncode != 0:
                acl_warning = True
        except Exception:
            acl_warning = True
    else:
        try:
            os.chmod(run_dir, 0o700)
        except OSError:
            acl_warning = True
    return run_dir, acl_warning


# ---------------------------------------------------------------------------
# Per-dispatch KIMI_CODE_HOME isolation (TOOL-013, issue #31)
# ---------------------------------------------------------------------------

_HOME_SEED_FILES = ("config.toml", "device_id", "region")
_HOME_SEED_DIRS = ("credentials",)


def create_isolated_home():
    """Create a per-dispatch isolated KIMI_CODE_HOME. Returns the path, or None
    when isolation is disabled (DELEGATE_NO_HOME_ISOLATION=1).

    Kimi Code CLI auto-registers every CWD it runs in as a workspace and writes
    sessions under KIMI_CODE_HOME; without isolation, every dispatched task
    pollutes the user's real ~/.kimi-code (phantom workspaces, session bloat).
    The isolated home is SEEDED with the operator's config and credentials —
    an empty home fails auth (verified 2026-08-25, kimi 0.34.0).

    Lifecycle: the CALLER owns the home after the run. Wire logs for cost
    metering live under <home>/sessions/, so delegate never deletes it;
    callers remove it after metering (see runner/pilot.py) and orphaned
    delegate-kimi-home-* dirs are swept by the caller side.
    """
    if os.environ.get("DELEGATE_NO_HOME_ISOLATION"):
        return None
    src = os.environ.get("KIMI_CODE_HOME") or os.path.join(
        os.environ.get("USERPROFILE", str(Path.home())), ".kimi-code")
    home = tempfile.mkdtemp(prefix="delegate-kimi-home-")
    for name in _HOME_SEED_FILES:
        s = os.path.join(src, name)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(home, name))
    for name in _HOME_SEED_DIRS:
        s = os.path.join(src, name)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(home, name))
    return home


# ---------------------------------------------------------------------------
# Output capture (reader threads)
# ---------------------------------------------------------------------------

_DEFAULT_MAX_LOG_BYTES = 67108864  # 64 MiB


class OutputReader(threading.Thread):
    """Reads a pipe, writes raw bytes to a log file (bounded), and keeps a bounded in-memory tail.

    A threading.Lock guards _tail mutation in run() and the snapshot in
    get_result() to prevent BufferError when a caller decodes while the reader
    is still resizing the bytearray (e.g. after a join timeout caused by a
    grandchild holding the pipe write handle).
    """

    def __init__(self, pipe, log_path, max_tail_bytes, max_log_bytes=_DEFAULT_MAX_LOG_BYTES):
        super().__init__(daemon=True)
        self._pipe = pipe
        self._log_path = log_path
        self._max_tail_bytes = max_tail_bytes
        self._max_log_bytes = max_log_bytes
        self._tail = bytearray()
        self._truncated = False
        self._log_truncated = False
        self._error = None
        self._lock = threading.Lock()
        self._log_bytes_written = 0

    def run(self):
        try:
            with open(self._log_path, "wb") as f:
                while True:
                    chunk = self._pipe.read(8192)
                    if not chunk:
                        break
                    # Write to disk log only if under the disk bound.
                    if self._log_bytes_written < self._max_log_bytes:
                        remaining = self._max_log_bytes - self._log_bytes_written
                        if len(chunk) <= remaining:
                            f.write(chunk)
                            self._log_bytes_written += len(chunk)
                        else:
                            f.write(chunk[:remaining])
                            self._log_bytes_written += remaining
                            self._log_truncated = True
                        f.flush()
                    # Update in-memory tail under lock.
                    with self._lock:
                        self._tail.extend(chunk)
                        if len(self._tail) > self._max_tail_bytes:
                            excess = len(self._tail) - self._max_tail_bytes
                            del self._tail[:excess]
                            self._truncated = True
        except Exception as e:
            self._error = e
        finally:
            try:
                self._pipe.close()
            except Exception:
                pass

    def get_result(self):
        """Return (decoded_tail, tail_truncated, log_truncated, error)."""
        with self._lock:
            snapshot = bytes(self._tail)
        text = snapshot.decode("utf-8", errors="replace")
        return text, self._truncated, self._log_truncated, self._error


# ---------------------------------------------------------------------------
# Kill sequence
# ---------------------------------------------------------------------------

def kill_process_tree(pid, grace_seconds, job=None):
    """Execute the full kill sequence.

    Order: graceful taskkill /T → grace wait (early return if child exits)
    → force taskkill /T /F (while parent-PID links are intact so /T can
    enumerate descendants) → close job handle (kernel terminates anything
    still in the job) → belt-and-braces force taskkill again.

    Does NOT call proc.terminate() — killing the root before tree enumeration
    orphans grandchildren.  The force taskkill /T /F runs BEFORE job close so
    that out-of-job descendants (created in the Popen→assign window) are still
    reachable via parent-PID links.
    """
    # Step 1: graceful taskkill (no /F) — sends WM_CLOSE; console processes
    # without a message loop ignore it, but it is cheap and non-destructive.
    try:
        subprocess.run(
            [_TASKKILL_EXE, "/PID", str(pid), "/T"],
            capture_output=True, timeout=10, shell=False,
            env=_MINIMAL_TOOL_ENV,
        )
    except Exception:
        pass

    # Step 2: grace period — poll for child exit so we don't sleep the full
    # grace if the child died immediately after step 1.
    if grace_seconds > 0:
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            # We cannot call proc.poll() here (no proc ref), so just sleep
            # in small increments. The caller will reap after we return.
            time.sleep(min(0.2, deadline - time.monotonic()))

    # Step 3: force taskkill /T /F while parent links are intact.
    # This catches descendants created before job assignment (the
    # Popen→assign window) that the job close cannot reach.
    try:
        subprocess.run(
            [_TASKKILL_EXE, "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=10, shell=False,
            env=_MINIMAL_TOOL_ENV,
        )
    except Exception:
        pass

    # Step 4: close job handle — kernel terminates anything still in the job.
    if job is not None:
        try:
            close_job(job)
        except Exception:
            pass

    # Step 5: belt-and-braces — force kill again after job close for any
    # process that survived both prior steps.
    try:
        subprocess.run(
            [_TASKKILL_EXE, "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=10, shell=False,
            env=_MINIMAL_TOOL_ENV,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Interruption handling
# ---------------------------------------------------------------------------

_interrupted = threading.Event()
_interrupt_condition = None


def _signal_handler(signum, frame):
    global _interrupt_condition
    _interrupted.set()
    _interrupt_condition = f"received signal {signum}"


def install_signal_handlers():
    """Install minimal signal handlers that only set a flag."""
    try:
        signal.signal(signal.SIGINT, _signal_handler)
    except (ValueError, OSError):
        pass
    try:
        signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, OSError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# Main run logic
# ---------------------------------------------------------------------------

def run_delegate(args):
    """Main entry point. Returns (result_dict, exit_code).

    Wrapped in a top-level exception guard so any escaping exception produces
    one sanitized internal_error JSON envelope (error = class name only).
    """
    start_time = time.monotonic()
    agent_name = args.agent
    try:
        return _run_delegate_inner(args, start_time, agent_name)
    except (ConfigError, InputError):
        raise  # already handled inside _run_delegate_inner
    except Exception as e:
        return _make_result("internal_error", agent=agent_name,
                            error=type(e).__name__), EXIT_INTERNAL
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt):
            raise
        return _make_result("internal_error", agent=agent_name,
                            error=type(e).__name__), EXIT_INTERNAL


def _run_delegate_inner(args, start_time, agent_name):
    """Actual run logic. Raises ConfigError/InputError for validation, returns (dict, code) otherwise."""

    # Load config
    try:
        cfg = load_config()
    except ConfigError as e:
        return _make_result("invalid", error=str(e), agent=agent_name), EXIT_INVALID

    # Find agent
    agents = cfg["agents"]
    if agent_name not in agents:
        return _make_result("invalid", error=f"Unknown agent: {agent_name}", agent=agent_name), EXIT_INVALID
    agent = agents[agent_name]

    # Lazily validate the invoked agent's executable (existence checks are deferred
    # from config load so an unrelated agent's missing CLI cannot block this run).
    try:
        _validate_agent(agent_name, agent, cfg["max_timeout_seconds"], check_executable=True)
    except ConfigError as e:
        return _make_result("invalid", error=str(e), agent=agent_name), EXIT_INVALID

    # Validate --resume-from and build the substituted resume argv. The invoked
    # agent must declare resume_args; substitution inserts the args immediately
    # after the executable: [command[0]] + resume_args + command[1:].
    resume_from = getattr(args, "resume_from", None)
    resume_argv = []
    if resume_from is not None:
        resume_args = agent.get("resume_args")
        if not resume_args:
            return _make_result(
                "invalid",
                error=f"Agent '{agent_name}' does not support resume (no resume_args configured)",
                agent=agent_name), EXIT_INVALID
        try:
            validate_session_id(resume_from)
        except InputError as e:
            return _make_result("invalid", error=str(e), agent=agent_name), EXIT_INVALID
        resume_argv = [a.replace(_RESUME_PLACEHOLDER, resume_from) for a in resume_args]

    # Validate task source
    if args.task is not None and args.task_file is not None:
        return _make_result("invalid", error="Cannot specify both --task and --task-file", agent=agent_name), EXIT_INVALID
    if args.task is None and args.task_file is None:
        return _make_result("invalid", error="Must specify either --task or --task-file", agent=agent_name), EXIT_INVALID

    # Read task
    try:
        if args.task_file is not None:
            if "\x00" in args.task_file:
                raise InputError("NUL byte in task-file path")
            task_path = Path(args.task_file)
            try:
                task_bytes = task_path.read_bytes()
            except OSError as e:
                raise InputError(f"Cannot read task file: {e}")
            try:
                task_text = task_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise InputError("Task file is not valid UTF-8")
        else:
            task_text = args.task
    except InputError as e:
        return _make_result("invalid", error=str(e), agent=agent_name), EXIT_INVALID

    # Validate task
    try:
        validate_task(task_text, cfg["max_task_bytes"])
    except InputError as e:
        return _make_result("invalid", error=str(e), agent=agent_name), EXIT_INVALID

    # Validate timeout
    try:
        timeout = validate_timeout(args.timeout, agent, cfg["max_timeout_seconds"])
    except InputError as e:
        return _make_result("invalid", error=str(e), agent=agent_name), EXIT_INVALID

    # Validate workspace
    try:
        workspace = validate_workspace(args.workspace, cfg["allowed_workspace_roots"])
    except InputError as e:
        return _make_result("invalid", error=str(e), agent=agent_name), EXIT_INVALID

    # Check required environment
    try:
        check_required_environment(agent.get("required_environment", []))
    except InputError as e:
        return _make_result("invalid", error=str(e), agent=agent_name), EXIT_INVALID

    # Build child environment
    child_env = build_child_environment(agent)
    # Per-dispatch isolated KIMI_CODE_HOME (TOOL-013). Injected directly by the
    # parent — the agent allowlist governs inheritance, not wrapper invariants.
    child_home = create_isolated_home()
    if child_home is not None:
        child_env["KIMI_CODE_HOME"] = child_home

    # Create run directory
    run_dir, acl_warning = create_run_dir()

    # Build argv — resume args (if any) go immediately after the executable,
    # before the agent's fixed args.
    argv = [agent["command"][0]] + resume_argv + list(agent["command"][1:])
    task_file_in_run_dir = None
    stdin_mode = agent["prompt_delivery"] == "stdin"
    if agent["prompt_delivery"] == "argument":
        argv.append(task_text)
    elif agent["prompt_delivery"] == "file":
        task_file_in_run_dir = os.path.join(run_dir, "task.txt")
        with open(task_file_in_run_dir, "w", encoding="utf-8") as f:
            f.write(task_text)
        argv.append(task_file_in_run_dir)

    # Launch — catch ValueError (NUL in env, etc.) alongside OSError
    try:
        proc = subprocess.Popen(
            argv,
            shell=False,
            cwd=workspace,
            env=child_env,
            stdin=subprocess.PIPE if stdin_mode else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, ValueError) as e:
        return _make_result("internal_error", error=f"Failed to launch child: {type(e).__name__}",
                            agent=agent_name, run_dir=run_dir, child_home=child_home), EXIT_INTERNAL

    # Anchor the deadline immediately after Popen so config/workspace/icacls
    # setup time does not eat the child's timeout budget.
    launch_time = time.monotonic()
    deadline = launch_time + timeout

    # Create Job Object and assign process.
    # Residual: there is a small window between Popen returning and
    # AssignProcessToJobObject completing where the child can spawn
    # descendants that never enter the job. stdlib subprocess has no
    # CREATE_SUSPENDED equivalent, so this window cannot be fully closed.
    # Mitigation: the kill sequence runs force taskkill /T /F BEFORE job
    # close, so out-of-job descendants are still reachable via parent-PID
    # links while the root is alive.
    job = None
    proc_handle = None
    job_warning = False
    if _IS_WINDOWS:
        try:
            job = create_kill_on_close_job(bool(agent.get("allow_breakaway", False)))
            proc_handle = assign_process_to_job(job, proc.pid)
        except Exception:
            if job is not None:
                try:
                    close_job(job)
                except Exception:
                    pass
                job = None
            if proc_handle is not None:
                try:
                    close_process_handle(proc_handle)
                except Exception:
                    pass
                proc_handle = None
            job_warning = True

    # Start reader threads
    max_log = cfg.get("max_log_bytes", _DEFAULT_MAX_LOG_BYTES)
    stdout_log = os.path.join(run_dir, "stdout.log")
    stderr_log = os.path.join(run_dir, "stderr.log")
    stdout_reader = OutputReader(proc.stdout, stdout_log, cfg["max_stdout_bytes"], max_log)
    stderr_reader = OutputReader(proc.stderr, stderr_log, cfg["max_stderr_bytes"], max_log)
    stdout_reader.start()
    stderr_reader.start()

    # Deliver task via stdin in a daemon thread: a child that never reads stdin
    # would otherwise block proc.stdin.write() before the wait loop starts,
    # silently defeating the timeout. Delivery is best-effort.
    stdin_thread = None
    if stdin_mode:
        def _write_stdin(p=proc, t=task_text):
            try:
                p.stdin.write(t.encode("utf-8"))
            except (OSError, ValueError):
                pass
            finally:
                try:
                    p.stdin.close()
                except (OSError, ValueError):
                    pass
        stdin_thread = threading.Thread(target=_write_stdin, daemon=True)
        stdin_thread.start()

    # Wait with timeout — poll child BEFORE deadline check so a child that
    # exits at the deadline is reported as completed, not timeout.
    timed_out = False
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
                timed_out = True
                break
            time.sleep(min(0.1, remaining))
    except KeyboardInterrupt:
        _interrupted.set()
        _interrupt_condition = "received KeyboardInterrupt"

    # Handle interruption
    if _interrupted.is_set():
        kill_process_tree(proc.pid, cfg["default_kill_grace_seconds"], job)
        job = None  # kill_process_tree closed it
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        stdout_reader.join(timeout=10)
        stderr_reader.join(timeout=10)
        if stdin_thread:
            stdin_thread.join(timeout=5)
        duration = time.monotonic() - start_time
        stdout_text, stdout_trunc, stdout_log_trunc, _ = stdout_reader.get_result()
        stderr_text, stderr_trunc, stderr_log_trunc, _ = stderr_reader.get_result()
        result = _make_result(
            "interrupted",
            agent=agent_name,
            duration=duration,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            stdout_trunc=stdout_trunc,
            stderr_trunc=stderr_trunc,
            stdout_log_trunc=stdout_log_trunc,
            stderr_log_trunc=stderr_log_trunc,
            run_dir=run_dir,
            acl_warning=acl_warning,
            job_warning=job_warning,
            child_session_id=extract_child_session_id(stdout_text, stderr_text),
            child_home=child_home,
            error=_interrupt_condition,
        )
        _cleanup_handles(proc_handle, job)
        return result, EXIT_INTERRUPTED

    # Handle timeout
    if timed_out:
        kill_process_tree(proc.pid, cfg["default_kill_grace_seconds"], job)
        job = None  # kill_process_tree closed it
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        stdout_reader.join(timeout=10)
        stderr_reader.join(timeout=10)
        if stdin_thread:
            stdin_thread.join(timeout=5)
        duration = time.monotonic() - start_time
        stdout_text, stdout_trunc, stdout_log_trunc, _ = stdout_reader.get_result()
        stderr_text, stderr_trunc, stderr_log_trunc, _ = stderr_reader.get_result()
        result = _make_result(
            "timeout",
            agent=agent_name,
            duration=duration,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            stdout_trunc=stdout_trunc,
            stderr_trunc=stderr_trunc,
            stdout_log_trunc=stdout_log_trunc,
            stderr_log_trunc=stderr_log_trunc,
            run_dir=run_dir,
            acl_warning=acl_warning,
            job_warning=job_warning,
            child_session_id=extract_child_session_id(stdout_text, stderr_text),
            child_home=child_home,
        )
        _cleanup_handles(proc_handle, job)
        return result, EXIT_TIMEOUT

    # Normal completion
    rc = proc.returncode
    stdout_reader.join(timeout=10)
    stderr_reader.join(timeout=10)
    if stdin_thread:
        stdin_thread.join(timeout=5)

    stdout_text, stdout_trunc, stdout_log_trunc, stdout_err = stdout_reader.get_result()
    stderr_text, stderr_trunc, stderr_log_trunc, stderr_err = stderr_reader.get_result()
    if stdout_err or stderr_err:
        duration = time.monotonic() - start_time
        result = _make_result(
            "internal_error",
            agent=agent_name,
            duration=duration,
            run_dir=run_dir,
            acl_warning=acl_warning,
            job_warning=job_warning,
            child_home=child_home,
            error=f"Reader thread error: {type(stdout_err or stderr_err).__name__}",
        )
        _cleanup_handles(proc_handle, job)
        return result, EXIT_INTERNAL

    duration = time.monotonic() - start_time
    status = "completed" if rc == 0 else "failed"
    result = _make_result(
        status,
        agent=agent_name,
        child_exit_code=rc,
        duration=duration,
        stdout_text=stdout_text,
        stderr_text=stderr_text,
        stdout_trunc=stdout_trunc,
        stderr_trunc=stderr_trunc,
        stdout_log_trunc=stdout_log_trunc,
        stderr_log_trunc=stderr_log_trunc,
        run_dir=run_dir,
        acl_warning=acl_warning,
        job_warning=job_warning,
        child_session_id=extract_child_session_id(stdout_text, stderr_text),
        child_home=child_home,
    )
    _cleanup_handles(proc_handle, job)
    return result, EXIT_OK


def _cleanup_handles(proc_handle, job):
    """Close process and job handles exactly once."""
    if proc_handle is not None:
        try:
            close_process_handle(proc_handle)
        except Exception:
            pass
    # job may already be closed by kill_process_tree; only close if still open.
    # Caller sets job = None after kill_process_tree, so this only fires on
    # normal-completion paths where the job was not closed.
    if job is not None:
        try:
            close_job(job)
        except Exception:
            pass


def _make_result(status, agent=None, child_exit_code=None, duration=None,
                 stdout_text="", stderr_text="", stdout_trunc=False, stderr_trunc=False,
                 stdout_log_trunc=False, stderr_log_trunc=False,
                 run_dir=None, acl_warning=False, job_warning=False,
                 child_session_id=None, child_home=None, error=None):
    """Build the JSON result envelope."""
    return {
        "schema_version": 1,
        "status": status,
        "agent": agent,
        "child_exit_code": child_exit_code,
        "child_session_id": child_session_id,
        "child_home": child_home,
        "duration_seconds": round(duration, 3) if duration is not None else None,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "stdout_truncated": stdout_trunc,
        "stderr_truncated": stderr_trunc,
        "stdout_log_truncated": stdout_log_trunc,
        "stderr_log_truncated": stderr_log_trunc,
        "run_dir": run_dir,
        "acl_warning": acl_warning,
        "job_warning": job_warning,
        "error": error,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

class _NoJsonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that does not print to stdout on --help.

    Standard argparse prints help to stdout and raises SystemExit(0).  We want
    --help to produce only help text (no JSON envelope) and exit 0.
    """

    def parse_args(self, args=None, namespace=None):
        try:
            return super().parse_args(args, namespace)
        except SystemExit as e:
            if e.code == 0:
                # --help: help was already printed, just exit 0
                raise
            # Argument error: argparse printed to stderr, emit invalid JSON
            result = _make_result("invalid", error="Invalid command-line arguments")
            sys.stdout.buffer.write(json.dumps(result).encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()
            sys.exit(EXIT_INVALID)


def main():
    install_signal_handlers()
    parser = _NoJsonArgumentParser(
        prog="delegate",
        description="Launch one configured external CLI worker with a bounded task.",
        add_help=True,
    )
    parser.add_argument("--agent", required=True, help="Configured agent name")
    parser.add_argument("--workspace", required=True, help="Workspace directory path")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--task", help="Task text")
    group.add_argument("--task-file", help="Path to a UTF-8 task file")
    parser.add_argument("--timeout", type=float, default=None, help="Timeout in seconds")
    parser.add_argument("--resume-from", dest="resume_from", default=None,
                        help="Resume the child CLI session with this id "
                             "(requires resume_args in the agent config)")

    try:
        args = parser.parse_args()
    except SystemExit as e:
        if e.code == 0:
            sys.exit(0)
        sys.exit(e.code if e.code is not None else EXIT_INVALID)

    result, exit_code = run_delegate(args)
    try:
        sys.stdout.buffer.write(json.dumps(result).encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
    except Exception:
        sys.exit(EXIT_INTERNAL)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
