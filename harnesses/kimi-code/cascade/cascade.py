#!/usr/bin/env python3
"""cascade — deterministic controller for the static model cascade.

Design authority: ``policy/STATIC_CASCADE_SPEC.md`` (v3). The controller is the
enforcement plane: the GLM orchestrator (a Kimi session) is a decision
component; ALL state transitions, caps, verifier-immutability checks, and
delegate invocations go through this CLI. Prompts don't enforce; code enforces.

Subcommands (all operate on a cascade directory, default ``<workspace>/.orchestrator``):

  init       validate the planner's task list, snapshot verifier hashes and a
             git checkpoint, write cascade-state.json atomically. Requires the
             owner-set --threat-model (immutable for the goal).
  dispatch   legality check -> build task packet -> invoke delegate (or emit a
             native-K3 dispatch packet) -> log -> update state. Evidence
             hardening: files_changed vs the checkpoint (tracked + untracked
             inventory), child_exit_code, and run_dir log archival with sha256
             are recorded in the task's evidence entry.
  record-qc  apply a structured QC verdict under the QC review cap. Accepting
             despite blocker/major findings requires --dismiss-reason; the
             findings are stored verbatim in the goal's dismissed_findings.
  verify     verifier-file immutability check, then run the deterministic
             verifier command ourselves (worker output is never trusted).
  status     one JSON line: progress, counters, cost vs ceiling, open rungs,
             threat model, dismissed findings.
  replan     replace the task list (requires --confirm and >=2 tasks failed
             with the same root-cause marker).
  commit-green  git add + commit the task's scope paths; only after the
             controller-run verify passed (never worker-reported green).
  rollback   scoped ``git checkout <checkpoint_ref> -- <scope paths>``; legal
             only when the task's last dispatch is in a terminal envelope state.
  handoff    bounded (8 KB hard cap) bootstrap packet for session resumes:
             goal, per-task status, counters, decisions log, evidence pointer.
  record-decision  append to the goal's append-only decisions log (decision,
             rationale, rejected alternatives, source user|leader).

Exit codes: 0 ok; 1 verification failed/rejected; 2 invalid input;
3 legality refusal (stdout carries {"allowed": false, "reason": ...});
4 infrastructure error (delegate transport/envelope failures).

Environment overrides (all optional):
  CASCADE_DELEGATE        path to the delegate wrapper script
                          (default: sibling ../delegate/delegate.py). Tests
                          point this at a fixture fake.
  CASCADE_AGENTS_CONFIG   path to a delegate agents.json used to validate
                          executor->profile mapping (default: DELEGATE_CONFIG,
                          else sibling ../delegate/agents.json).
  CASCADE_STATE_DIR       override the cascade directory
                          (default: <workspace>/.orchestrator).
  CASCADE_RETRY_BACKOFF   seconds before the single transient retry (default 2).

Stdlib only, Python 3.10. Git is mutated only by the explicit ``commit-green``
and ``rollback`` subcommands (plus ``git stash create`` checkpoints, which
mutate nothing); every other git invocation is read-only.
"""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants (spec §5, §7)
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_VERIFY_FAILED = 1
EXIT_INVALID = 2
EXIT_REFUSED = 3
EXIT_INFRA = 4

# Executor name -> delegate profile. "k3" is native (a Kimi subagent run by the
# orchestrator), never a delegate profile.
EXECUTOR_PROFILES = {
    "flash": "ds-flash-worker",
    "pro": "ds-pro-worker",
    "k27": "k27-worker",
    "k3": None,
}
EXECUTORS = tuple(EXECUTOR_PROFILES)

# Vision capability (spec §9.1, v3.1): capability routing, NOT escalation —
# an image task is impossible for a text-only model at any retry depth.
# GLM/DeepSeek are text-only per FireConnect docs; K2.7-code was verified
# vision-capable live 2026-08-13; K3 is vision-capable.
VISION_CAPABLE = {"flash": False, "pro": False, "k27": True, "k3": True}
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"})

ADVISOR_PROFILES = {
    "codex": "codex-advisor",
    "claude": "claude-advisor",
}

# Per-task caps (spec §7 budget arithmetic).
EXECUTOR_CAP = {"normal": 5, "high": 6}  # 2 assigned + 2 K3 + 1 post-advisor K3 (+1 high)
QC_CAP = {"normal": 2, "high": 3}
PLANNER_REPLAN_CAP = 2  # planner_calls + replan_calls, per goal

# Escalation ladder rungs (spec §5): assigned -> k3 -> advisor -> post-advisor k3.
RUNG_NAMES = ["assigned", "k3", "advisor", "k3_post_advisor"]
RUNG_ASSIGNED, RUNG_K3, RUNG_ADVISOR, RUNG_K3_POST_ADVISOR = 0, 1, 2, 3
# Post-advisor K3 retry budget: 1 normal / 2 high (spec §7: cap 5 = 2+2+1, 6 = +1).
POST_ADVISOR_BUDGET = {"normal": 1, "high": 2}

MAX_OUTPUT_TAIL_CHARS = 2000      # evidence tails stored in state
VERIFY_OUTPUT_TAIL_BYTES = 65536  # bounded verifier output (tail kept)
DEFAULT_VERIFY_TIMEOUT = 300

TERMINAL_TASK_STATUSES = ("done", "failed")
DISPATCHABLE_STATUSES = ("ready", "in_progress")
# Envelope statuses in which a dispatch is finished and the tree is stable;
# rollback is legal only when the task's last dispatch ended in one of these.
TERMINAL_ENVELOPE_STATUSES = ("completed", "failed", "timeout")

# Owner-set at init (plan v2 F8); immutable for the goal.
THREAT_MODELS = ("single-operator", "adversarial-local", "hostile-input")

FINDING_REQUIRED_KEYS = ("severity", "location", "claim")
BLOCKING_SEVERITIES = ("blocker", "major")

STATE_FILE = "cascade-state.json"
LOG_FILE = "cascade-log.jsonl"
EVIDENCE_DIR_NAME = "evidence"

HANDOFF_MAX_BYTES = 8192   # hard cap for the handoff bootstrap packet
MAX_FILES_CHANGED = 500    # cap for files_changed/untracked lists in evidence


class CascadeError(Exception):
    """Invalid input / unreadable files. Maps to EXIT_INVALID."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or []


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _print_json(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _refuse(reason, **extra):
    out = {"allowed": False, "reason": reason}
    out.update(extra)
    _print_json(out)
    return EXIT_REFUSED


def _error(message, **extra):
    out = {"error": message}
    out.update(extra)
    _print_json(out)


def _cascade_dir(workspace):
    env = os.environ.get("CASCADE_STATE_DIR")
    if env:
        return Path(env)
    return Path(workspace) / ".orchestrator"


def _state_path(workspace):
    return _cascade_dir(workspace) / STATE_FILE


def _log_path(workspace):
    return _cascade_dir(workspace) / LOG_FILE


def _load_state(workspace):
    path = _state_path(workspace)
    if not path.is_file():
        raise CascadeError(f"cascade state not found: {path} (run 'init' first)")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise CascadeError(f"cascade state is not valid JSON: {e}")
    if not isinstance(state, dict) or not isinstance(state.get("tasks"), list):
        raise CascadeError("cascade state is malformed")
    return state


def _write_json_atomic(path, data):
    """Write JSON atomically: temp file in the same directory + os.replace.

    On any failure the temp file is removed and the pre-existing destination
    (if any) is left untouched — no partial state files.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _save_state(workspace, state):
    _write_json_atomic(_state_path(workspace), state)


def _append_log(workspace, entry):
    entry = dict(entry)
    entry.setdefault("timestamp", _now())
    path = _log_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _find_task(state, task_id):
    for t in state["tasks"]:
        if t.get("task_id") == task_id:
            return t
    return None


def _safe_resolve(workspace, rel):
    """Resolve a plan-relative path under the workspace. None if it escapes."""
    if not isinstance(rel, str) or "\x00" in rel:
        return None
    try:
        p = (Path(workspace) / rel).resolve()
    except (OSError, ValueError):
        return None
    ws_norm = os.path.normcase(str(Path(workspace).resolve()))
    p_norm = os.path.normcase(str(p))
    if p_norm == ws_norm or p_norm.startswith(ws_norm.rstrip(os.sep) + os.sep):
        return p
    return None


def _scope_has_images(workspace, scope):
    """True if any scope entry names an image file (by extension, case-
    insensitive) or is an existing directory containing one."""
    for s in scope:
        if not isinstance(s, str):
            continue
        if Path(s).suffix.lower() in IMAGE_EXTENSIONS:
            return True
        p = _safe_resolve(workspace, s)
        if p is not None and p.is_dir():
            for _, _, files in os.walk(p):
                if any(Path(f).suffix.lower() in IMAGE_EXTENSIONS for f in files):
                    return True
    return False


def _task_vision_bearing(task, workspace):
    """Vision-bearing = explicit ``vision: true`` or image files in scope (§9.1)."""
    if task.get("vision") is True:
        return True
    scope = task.get("scope")
    return isinstance(scope, list) and _scope_has_images(workspace, scope)


def _git_checkpoint(workspace):
    """Return a rollback ref via `git stash create` (mutates nothing).

    Falls back to HEAD when the tree is clean (stash create prints nothing),
    and to None when the workspace is not a repo or git fails — callers then
    rely on the verifier file-hash snapshot instead.
    """
    try:
        probe = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--git-dir"],
            capture_output=True, timeout=30, shell=False,
        )
        if probe.returncode != 0:
            return None
        created = subprocess.run(
            ["git", "-C", str(workspace), "stash", "create"],
            capture_output=True, timeout=30, shell=False,
        )
        if created.returncode == 0:
            ref = created.stdout.decode("utf-8", errors="replace").strip()
            if ref:
                return ref
        head = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"],
            capture_output=True, timeout=30, shell=False,
        )
        if head.returncode == 0:
            ref = head.stdout.decode("utf-8", errors="replace").strip()
            if ref:
                return ref
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _git_run(workspace, argv, timeout=30):
    """Run one git command; returns (returncode, stdout_text, stderr_text)."""
    proc = subprocess.run(
        ["git", "-C", str(workspace), *argv],
        capture_output=True, timeout=timeout, shell=False,
    )
    return (proc.returncode,
            proc.stdout.decode("utf-8", errors="replace"),
            proc.stderr.decode("utf-8", errors="replace"))


def _git_is_repo(workspace):
    try:
        rc, _, _ = _git_run(workspace, ["rev-parse", "--git-dir"])
        return rc == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _git_has_head(workspace):
    """True only for a repo with at least one commit.

    A HEAD-less repo (never committed) cannot produce meaningful checkpoints,
    diffs, or checkouts — treat it as non-git for evidence/rollback/commit
    purposes rather than guess.
    """
    if not _git_is_repo(workspace):
        return False
    try:
        rc, _, _ = _git_run(workspace, ["rev-parse", "--verify", "HEAD"])
        return rc == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _git_files_changed(workspace, checkpoint_ref):
    """Tracked changes vs checkpoint_ref plus an untracked-file inventory.

    Returns {"tracked": [...], "untracked": [...], "vs": ref} or None when the
    workspace is not a usable git repo / git fails — the checkpoint ref alone
    (a dangling stash commit) is blind to files the worker newly created, so
    the untracked inventory is computed separately via ls-files.
    """
    if not _git_has_head(workspace):
        return None
    tracked = []
    try:
        if checkpoint_ref:
            rc, out, _ = _git_run(workspace, ["diff", "--name-only", checkpoint_ref, "--"])
            if rc != 0:
                return None
            tracked = [l for l in out.splitlines() if l.strip()]
        rc, out, _ = _git_run(workspace, ["ls-files", "--others", "--exclude-standard"])
        if rc != 0:
            return None
        untracked = [l for l in out.splitlines() if l.strip()]
    except (OSError, subprocess.TimeoutExpired):
        return None
    result = {
        "vs": checkpoint_ref,
        "tracked": tracked[:MAX_FILES_CHANGED],
        "untracked": untracked[:MAX_FILES_CHANGED],
    }
    if len(tracked) > MAX_FILES_CHANGED or len(untracked) > MAX_FILES_CHANGED:
        result["truncated"] = True
    return result


def _archive_run_logs(workspace, task, envelope):
    """Copy the dispatch run_dir's stdout/stderr logs into the evidence dir.

    Target: <cascade_dir>/evidence/<task_id>-<n>/ where n is the 1-based index
    of the evidence entry about to be appended (unique per dispatch). Returns
    the evidence fields to merge into the entry; all values are None when the
    envelope carries no run_dir (e.g. the fixture fake).
    """
    fields = {"run_dir": envelope.get("run_dir"),
              "evidence_dir": None, "stdout_sha256": None, "stderr_sha256": None}
    run_dir = envelope.get("run_dir")
    if not run_dir or not os.path.isdir(run_dir):
        return fields
    dest = _cascade_dir(workspace) / EVIDENCE_DIR_NAME / f"{task['task_id']}-{len(task['evidence']) + 1}"
    dest.mkdir(parents=True, exist_ok=True)
    for name, key in (("stdout.log", "stdout_sha256"), ("stderr.log", "stderr_sha256")):
        src = os.path.join(run_dir, name)
        if not os.path.isfile(src):
            continue
        shutil.copyfile(src, dest / name)
        fields[key] = _sha256_file(dest / name)
    fields["evidence_dir"] = str(dest)
    return fields


# ---------------------------------------------------------------------------
# Delegate integration
# ---------------------------------------------------------------------------

def _repo_root():
    return Path(__file__).resolve().parent.parent


def _delegate_path():
    env = os.environ.get("CASCADE_DELEGATE")
    if env:
        return Path(env)
    return _repo_root() / "delegate" / "delegate.py"


def _agents_config_path():
    env = os.environ.get("CASCADE_AGENTS_CONFIG") or os.environ.get("DELEGATE_CONFIG")
    if env:
        return Path(env)
    return _repo_root() / "delegate" / "agents.json"


def _load_agent_names():
    """Return the set of configured delegate profile names (empty on failure)."""
    path = _agents_config_path()
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return set()
    agents = cfg.get("agents")
    return set(agents) if isinstance(agents, dict) else set()


def _invoke_delegate(profile, workspace, packet_path, timeout, resume_from=None):
    """Invoke the delegate wrapper as a subprocess. Returns (envelope, error).

    On success ``envelope`` is the parsed dict and ``error`` is None. On any
    transport/parse failure ``envelope`` is None and ``error`` is a short
    machine-readable string. Never raises for child-side failures.
    """
    delegate = _delegate_path()
    cmd = [sys.executable, str(delegate),
           "--agent", profile,
           "--workspace", str(workspace),
           "--task-file", str(packet_path)]
    if timeout is not None:
        cmd += ["--timeout", str(timeout)]
    if resume_from:
        cmd += ["--resume-from", resume_from]

    # Backstop timeout: the wrapper enforces its own timeout; ours is only a
    # hung-wrapper guard (wrapper ceiling ~= timeout + grace + ~30s).
    backstop = (float(timeout) if timeout is not None else 1800.0) + 180.0
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=backstop, shell=False,
        )
    except subprocess.TimeoutExpired:
        return {"schema_version": 1, "status": "timeout", "agent": profile,
                "child_exit_code": None, "duration_seconds": backstop,
                "stdout": "", "stderr": "cascade backstop timeout",
                "error": "cascade_backstop_timeout", "child_session_id": None}, None
    except OSError as e:
        return None, f"delegate_launch_failed:{type(e).__name__}"

    out = proc.stdout.decode("utf-8", errors="replace").strip()
    envelope = None
    # The wrapper emits exactly one JSON envelope line; tolerate leading noise
    # by trying the whole output first, then the last non-empty line.
    for candidate in ([out] if out else []) + [l for l in out.splitlines() if l.strip()][-1:]:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("status"), str):
            envelope = parsed
            break
    if envelope is None:
        return None, "delegate_envelope_parse_error"
    return envelope, None


# ---------------------------------------------------------------------------
# Plan validation (mirrors cascade-schema.json; stdlib, no jsonschema dep)
# ---------------------------------------------------------------------------

def validate_plan(plan, workspace, agent_names):
    """Validate the planner's task list. Returns a list of error strings."""
    errors = []
    if not isinstance(plan, dict):
        return ["plan root must be an object"]

    goal = plan.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        errors.append("goal must be a non-empty string")

    ceiling = plan.get("cost_ceiling_usd")
    if not _is_num(ceiling) or ceiling <= 0:
        errors.append("cost_ceiling_usd must be a positive number (user-supplied until history exists)")

    est = plan.get("k3_direct_cost_estimate_usd")
    if est is not None and not _is_num(est):
        errors.append("k3_direct_cost_estimate_usd must be a number or null")

    verifier_set = plan.get("verifier_set")
    if (not isinstance(verifier_set, list) or not verifier_set
            or any(not isinstance(v, str) or not v.strip() for v in verifier_set)):
        errors.append("verifier_set must be a non-empty array of workspace-relative path strings")
        verifier_set = []
    for v in verifier_set:
        p = _safe_resolve(workspace, v)
        if p is None:
            errors.append(f"verifier path escapes the workspace: {v!r}")
        elif not p.is_file():
            errors.append(f"verifier file not found: {v!r}")

    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        errors.append("tasks must be a non-empty array")
        tasks = []

    seen_ids = set()
    for i, t in enumerate(tasks):
        where = f"tasks[{i}]"
        if not isinstance(t, dict):
            errors.append(f"{where} must be an object")
            continue
        tid = t.get("task_id")
        if not isinstance(tid, str) or not tid.strip():
            errors.append(f"{where}.task_id must be a non-empty string")
        elif tid in seen_ids:
            errors.append(f"{where}.task_id is duplicated: {tid!r}")
        else:
            seen_ids.add(tid)

        if not isinstance(t.get("objective"), str) or not t.get("objective", "").strip():
            errors.append(f"{where}.objective must be a non-empty string")

        executor = t.get("executor", "flash")
        if executor not in EXECUTORS:
            errors.append(f"{where}.executor must be one of {list(EXECUTORS)}")
        else:
            profile = EXECUTOR_PROFILES[executor]
            if profile is not None and profile not in agent_names:
                errors.append(
                    f"{where}.executor {executor!r} maps to delegate profile {profile!r}, "
                    f"which is not configured in {_agents_config_path()}")
        if executor == "pro":
            pro_reason = t.get("pro_reason")
            if not isinstance(pro_reason, str) or not pro_reason.strip():
                errors.append(f"{where}.pro_reason is required (non-empty) when executor is 'pro'")

        vision = t.get("vision", False)
        if not isinstance(vision, bool):
            errors.append(f"{where}.vision must be a boolean when present")
            vision = False
        scope_raw = t.get("scope")
        vision_bearing = vision or (
            isinstance(scope_raw, list) and _scope_has_images(workspace, scope_raw))
        if executor in EXECUTORS and vision_bearing and not VISION_CAPABLE[executor]:
            errors.append(
                f"{where}: vision-bearing task (image files in scope or vision: true) "
                f"requires a vision-capable executor (k27|k3), got {executor!r} "
                f"(spec §9.1 v3.1 — modality is a capability filter, not an escalation rung)")

        criticality = t.get("criticality", "normal")
        if criticality not in ("normal", "high"):
            errors.append(f"{where}.criticality must be 'normal' or 'high'")

        max_attempts = t.get("max_attempts", 2)
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) \
                or not (1 <= max_attempts <= 2):
            errors.append(f"{where}.max_attempts must be an integer in [1, 2] (per-rung budget)")

        verification = t.get("verification")
        if not isinstance(verification, dict):
            errors.append(f"{where}.verification must be an object")
        else:
            det = verification.get("deterministic")
            qc = verification.get("qc_review")
            has_det = isinstance(det, str) and bool(det.strip())
            has_qc = qc is True
            if has_det == has_qc:
                errors.append(
                    f"{where}.verification must be exactly one of "
                    f'{{"deterministic": "<command>"}} or {{"qc_review": true}}')

        scope = t.get("scope")
        if not isinstance(scope, list) or any(not isinstance(s, str) or not s.strip() for s in scope):
            errors.append(f"{where}.scope must be an array of workspace-relative path strings")
        else:
            for s in scope:
                if _safe_resolve(workspace, s) is None:
                    errors.append(f"{where}.scope path escapes the workspace: {s!r}")
    return errors


def _new_task_entry(t):
    return {
        "task_id": t["task_id"],
        "objective": t["objective"],
        "executor": t.get("executor", "flash"),
        "pro_reason": t.get("pro_reason"),
        "verification": t["verification"],
        "scope": t.get("scope", []),
        "criticality": t.get("criticality", "normal"),
        "max_attempts": t.get("max_attempts", 2),
        "vision": t.get("vision", False),
        "attempts": 0,
        "rung": 0,
        "rung_attempts": 0,
        "status": "ready",
        "checkpoint_ref": None,
        "qc_reviews": 0,
        "resume_session_id": None,
        "force_escalate": False,
        "evidence": [],
        "root_cause": None,
        "failure_reason": None,
        "verification_passed": False,
        "verify_result": None,
        "failure_signatures": [],
    }


def _build_state(plan, workspace, threat_model):
    verifier_hashes = {}
    for v in plan["verifier_set"]:
        verifier_hashes[v] = _sha256_file(_safe_resolve(workspace, v))
    ref = _git_checkpoint(workspace)
    ceiling = float(plan["cost_ceiling_usd"])
    return {
        "schema_version": 1,
        "goal": plan["goal"],
        "created_at": plan.get("created_at") or _now(),
        "workspace": str(Path(workspace).resolve()),
        "threat_model": threat_model,  # owner-set at init; immutable for the goal
        "k3_direct_cost_estimate_usd": plan.get("k3_direct_cost_estimate_usd"),
        "verifier_set": list(plan["verifier_set"]),
        "verifier_hashes": verifier_hashes,
        "tasks": [_new_task_entry(t) for t in plan["tasks"]],
        "counters": {
            "executor_attempts": 0,
            "qc_reviews": 0,
            "planner_calls": 1,
            "replan_calls": 0,
            "advisor_calls": 0,
        },
        "cost_used_usd": float(plan.get("cost_used_usd", 0.0)),
        "cost_ceiling_usd": ceiling,
        "cost_warning_usd": round(0.5 * ceiling, 6),
        "checkpoint": {
            "mode": "git" if ref else "file_hashes",
            "ref": ref,
        },
        "verifier_defect_suspect": False,
        "decisions": [],            # append-only; written by record-decision
        "dismissed_findings": [],   # blocker/major findings dismissed via record-qc --dismiss-reason
    }


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def cmd_init(args):
    workspace = Path(args.workspace).resolve()
    if _state_path(workspace).is_file():
        _error("already_initialized",
               detail=f"{_state_path(workspace)} exists; use 'replan' to replace the task list")
        return EXIT_INVALID
    try:
        plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    except OSError as e:
        _error("plan_file_unreadable", detail=str(e))
        return EXIT_INVALID
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        _error("plan_file_invalid_json", detail=str(e))
        return EXIT_INVALID

    errors = validate_plan(plan, workspace, _load_agent_names())
    if errors:
        _error("plan_validation_failed", details=errors)
        return EXIT_INVALID

    state = _build_state(plan, workspace, args.threat_model)
    _save_state(workspace, state)
    _append_log(workspace, {
        "event": "init",
        "token_class": "planning",
        "tasks": len(state["tasks"]),
        "threat_model": state["threat_model"],
        "checkpoint_mode": state["checkpoint"]["mode"],
        "checkpoint_ref": state["checkpoint"]["ref"],
        "cost_ceiling_usd": state["cost_ceiling_usd"],
        "cost_usd": None,
    })
    _print_json({
        "initialized": True,
        "tasks": len(state["tasks"]),
        "state_file": str(_state_path(workspace)),
        "threat_model": state["threat_model"],
        "checkpoint_mode": state["checkpoint"]["mode"],
        "checkpoint_ref": state["checkpoint"]["ref"],
        "cost_ceiling_usd": state["cost_ceiling_usd"],
        "cost_warning_usd": state["cost_warning_usd"],
    })
    return EXIT_OK


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def _rung_budget(task, rung):
    """Attempt budget for one rung. Budgets reset on escalation (spec §5)."""
    if rung in (RUNG_ASSIGNED, RUNG_K3):
        return task["max_attempts"]
    if rung == RUNG_ADVISOR:
        return 1  # single advisor consultation, then adjudicate
    return POST_ADVISOR_BUDGET[task["criticality"]]


def _advance_rung(task, force=False):
    """Apply forced/exhausted-rung escalation. Returns True if the rung changed."""
    changed = False
    if force or task.get("force_escalate"):
        task["rung"] += 1
        task["rung_attempts"] = 0
        task["force_escalate"] = False
        changed = True
    while task["rung"] <= RUNG_K3_POST_ADVISOR \
            and task["rung_attempts"] >= _rung_budget(task, task["rung"]):
        task["rung"] += 1
        task["rung_attempts"] = 0
        changed = True
    return changed


def _build_packet(workspace, state, task, rung, reason):
    """Write the task packet file; returns its path."""
    packets = _cascade_dir(workspace) / "packets"
    packets.mkdir(parents=True, exist_ok=True)
    path = packets / f"{task['task_id']}-a{task['attempts'] + 1}-r{rung}.txt"

    verification = task["verification"]
    if isinstance(verification.get("deterministic"), str):
        ver_line = f"deterministic (run by the orchestrator, never trust self-reports): {verification['deterministic']}"
    else:
        ver_line = "qc_review: structured QC review (severity/location/claim/evidence/minimal fix)"

    lines = [
        f"# Task packet: {task['task_id']}",
        f"goal: {state['goal']}",
        f"rung: {rung} ({RUNG_NAMES[rung]}); attempt {task['rung_attempts'] + 1} at this rung",
        f"criticality: {task['criticality']}",
        "",
        "## Objective",
        task["objective"],
        "",
        "## Scope (only these paths may change)",
        *[f"- {s}" for s in task["scope"]],
        "",
        "## Verification",
        ver_line,
    ]
    if reason:
        lines += ["", "## Dispatch reason", reason]
    if task["evidence"]:
        lines += ["", "## Evidence from prior attempts"]
        for i, ev in enumerate(task["evidence"], 1):
            lines.append(f"### prior {i}: rung {ev.get('rung')} ({ev.get('executor')}) — {ev.get('status')}")
            if ev.get("detail"):
                lines.append(ev["detail"])
    lines += [
        "",
        "## Rules",
        "- Modify only files under Scope. Never modify verifier files.",
        "- Report exactly what you changed; unverified claims are rejected.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _executor_for_rung(task, rung, advisor_kind):
    """Return (executor_label, delegate_profile_or_None) for a rung."""
    if rung == RUNG_ASSIGNED:
        executor = task["executor"]
        return executor, EXECUTOR_PROFILES[executor]
    if rung == RUNG_ADVISOR:
        return advisor_kind, ADVISOR_PROFILES[advisor_kind]
    return "k3", None  # RUNG_K3 and RUNG_K3_POST_ADVISOR are native K3


def cmd_dispatch(args):
    try:
        state = _load_state(args.workspace)
    except CascadeError as e:
        _error(str(e))
        return EXIT_INVALID
    workspace = Path(state["workspace"])

    task = _find_task(state, args.task)
    if task is None:
        return _refuse("unknown_task", task_id=args.task)
    if task["status"] not in DISPATCHABLE_STATUSES:
        return _refuse("task_status_not_dispatchable",
                       task_id=task["task_id"], status=task["status"])

    # Cost ceiling (spec §7): stop at ceiling, warn at 50%. cost_used_usd is
    # maintained externally (evals/meter.py); the controller enforces the gate.
    cost_used = float(state.get("cost_used_usd", 0.0))
    ceiling = float(state["cost_ceiling_usd"])
    if cost_used >= ceiling:
        return _refuse("cost_ceiling_exceeded",
                       cost_used_usd=cost_used, cost_ceiling_usd=ceiling)
    cost_warning = cost_used >= float(state.get("cost_warning_usd", 0.5 * ceiling))

    advisor_kind = args.advisor
    escalating = _advance_rung(task, force=args.escalate)
    if task["rung"] > RUNG_K3_POST_ADVISOR:
        task["status"] = "failed"
        task["failure_reason"] = "ladder_exhausted"
        _save_state(workspace, state)
        return _refuse("ladder_exhausted", task_id=task["task_id"],
                       detail="assigned -> k3 -> advisor -> post-advisor k3 all spent; task stopped")

    rung = task["rung"]
    executor, profile = _executor_for_rung(task, rung, advisor_kind)
    if rung == RUNG_ADVISOR and profile not in _load_agent_names():
        _error("advisor_profile_missing",
               detail=f"{profile} not in {_agents_config_path()}")
        return EXIT_INFRA

    # Defensive re-check of the vision capability filter (spec §9.1 v3.1):
    # scope contents may have changed since init. Only the assigned rung can
    # violate it — rungs 1/3 are K3 (vision-capable), rung 2 is read-only advice.
    if rung == RUNG_ASSIGNED and not VISION_CAPABLE[task["executor"]] \
            and _task_vision_bearing(task, workspace):
        return _refuse("vision_capability_violation",
                       task_id=task["task_id"], executor=task["executor"],
                       detail=("vision-bearing task (image files in scope or vision: true) "
                               "requires a vision-capable executor (k27|k3); replan or "
                               "--escalate to the k3 rung"))

    # First cap hit stops the task (spec §7). Advisor rung is quota-tracked,
    # not counted against the metered executor-attempt cap.
    if rung != RUNG_ADVISOR and task["attempts"] >= EXECUTOR_CAP[task["criticality"]]:
        task["status"] = "failed"
        task["failure_reason"] = "executor_cap_exhausted"
        _save_state(workspace, state)
        return _refuse("executor_cap_exhausted",
                       task_id=task["task_id"], attempts=task["attempts"],
                       cap=EXECUTOR_CAP[task["criticality"]])

    # Checkpoint before each dispatch (spec §8).
    task["checkpoint_ref"] = _git_checkpoint(workspace)

    packet = _build_packet(workspace, state, task, rung, args.reason)

    base_log = {
        "event": "dispatch",
        "task_id": task["task_id"],
        "executor": executor,
        "delegate_profile": profile,
        "rung": rung,
        "rung_name": RUNG_NAMES[rung],
        "token_class": "execution",
        "trigger": args.reason,
        "cost_usd": None,
        "tokens": {"uncached_in": None, "cached_in": None, "out": None},
    }

    if profile is None:
        # Native K3 rung: the controller cannot spawn a native subagent; it
        # counts the attempt, writes the packet, and instructs the orchestrator.
        task["attempts"] += 1
        task["rung_attempts"] += 1
        task["status"] = "in_progress"
        state["counters"]["executor_attempts"] += 1
        # Non-terminal evidence entry: the K3 subagent runs after dispatch
        # returns, so rollback must treat this rung as in-flight until a
        # terminal delegate envelope or controller verdict supersedes it.
        task["evidence"].append({
            "timestamp": _now(),
            "rung": rung,
            "executor": "k3",
            "status": "native_dispatch",
            "detail": f"packet written: {packet}",
            "child_session_id": None,
        })
        envelope = {
            "schema_version": 1,
            "status": "native_dispatch",
            "agent": "k3-native",
            "child_exit_code": None,
            "duration_seconds": None,
            "child_session_id": None,
            "task_packet": str(packet),
            "instructions": ('Run K3 as a FRESH native subagent (model="secondary") with this '
                             "packet, then call 'cascade.py verify'. The attempt is already counted."),
            "error": None,
        }
        _append_log(workspace, {**base_log, "delegate_status": "native_dispatch",
                                "duration_seconds": None, "child_session_id": None,
                                "resume_used": False})
        _save_state(workspace, state)
        if cost_warning:
            print(f"WARNING: cost_used_usd {cost_used} >= 50% of ceiling {ceiling}",
                  file=sys.stderr)
        _print_json(envelope)
        return EXIT_OK

    # Delegate-backed rung (assigned executor or advisor).
    resume_from = None
    if (not escalating and rung != RUNG_ADVISOR and task["rung_attempts"] > 0
            and task.get("resume_session_id")):
        # Within-rung retry: reuse the child session (spec §9.7); escalation is
        # always a fresh dispatch.
        resume_from = task["resume_session_id"]

    envelope, err = _invoke_delegate(profile, workspace, packet, args.timeout, resume_from)

    if err == "delegate_envelope_parse_error":
        # Infrastructure failure (spec §8): NOT counted toward max_attempts.
        _append_log(workspace, {**base_log, "delegate_status": "envelope_parse_error",
                                "duration_seconds": None, "child_session_id": None,
                                "resume_used": bool(resume_from)})
        _save_state(workspace, state)
        _error("delegate_envelope_parse_error",
               detail="delegate stdout was not a single JSON envelope; attempt not counted")
        return EXIT_INFRA
    if err is not None:
        _append_log(workspace, {**base_log, "delegate_status": err,
                                "duration_seconds": None, "child_session_id": None,
                                "resume_used": bool(resume_from)})
        _save_state(workspace, state)
        _error(err, detail="delegate failed to launch; attempt not counted")
        return EXIT_INFRA

    # Transient transport failure: retry once with backoff, never counted (§8).
    if envelope.get("status") == "internal_error":
        backoff = float(os.environ.get("CASCADE_RETRY_BACKOFF", "2"))
        if backoff > 0:
            time.sleep(backoff)
        envelope2, err2 = _invoke_delegate(profile, workspace, packet, args.timeout, resume_from)
        if err2 is None and envelope2.get("status") != "internal_error":
            envelope = envelope2
        else:
            _append_log(workspace, {**base_log, "delegate_status": "internal_error",
                                    "duration_seconds": envelope.get("duration_seconds"),
                                    "child_session_id": None,
                                    "resume_used": bool(resume_from)})
            _save_state(workspace, state)
            _error("delegate_internal_error",
                   detail="transient failure persisted after one retry; attempt not counted")
            return EXIT_INFRA

    status = envelope["status"]
    if status in ("invalid", "interrupted"):
        # Config/interruption: not a task attempt (§8).
        _append_log(workspace, {**base_log, "delegate_status": status,
                                "duration_seconds": envelope.get("duration_seconds"),
                                "child_session_id": None,
                                "resume_used": bool(resume_from)})
        _save_state(workspace, state)
        _error(f"delegate_{status}", detail=envelope.get("error"))
        return EXIT_INFRA

    # completed / failed / timeout: all count as attempts (timeout counts per
    # §8 — the worker may have made partial progress; inspect the checkpoint).
    if rung == RUNG_ADVISOR:
        state["counters"]["advisor_calls"] += 1
        task["rung_attempts"] += 1
    else:
        task["attempts"] += 1
        task["rung_attempts"] += 1
        state["counters"]["executor_attempts"] += 1
    task["status"] = "in_progress"

    child_session_id = envelope.get("child_session_id")
    if child_session_id and rung != RUNG_ADVISOR:
        task["resume_session_id"] = child_session_id

    tail = (envelope.get("stdout") or "")[-MAX_OUTPUT_TAIL_CHARS:]
    entry = {
        "timestamp": _now(),
        "rung": rung,
        "executor": executor,
        "status": status,
        "detail": tail,
        "child_session_id": child_session_id,
        "child_exit_code": envelope.get("child_exit_code"),
    }
    # F2: files_changed vs the per-dispatch checkpoint (tracked diff PLUS an
    # untracked-file inventory — the checkpoint ref is blind to new files).
    files_changed = _git_files_changed(workspace, task["checkpoint_ref"])
    entry["files_changed"] = (
        files_changed if files_changed is not None else "unavailable_not_a_git_repo")
    # F3: archive the run_dir logs into the evidence dir with sha256 pointers
    # so they survive temp cleanup.
    entry.update(_archive_run_logs(workspace, task, envelope))
    task["evidence"].append(entry)

    _append_log(workspace, {**base_log,
                            "delegate_status": status,
                            "duration_seconds": envelope.get("duration_seconds"),
                            "child_session_id": child_session_id,
                            "resume_used": bool(resume_from)})
    _save_state(workspace, state)

    if cost_warning:
        print(f"WARNING: cost_used_usd {cost_used} >= 50% of ceiling {ceiling}",
              file=sys.stderr)
    _print_json(envelope)
    return EXIT_OK


# ---------------------------------------------------------------------------
# record-qc
# ---------------------------------------------------------------------------

def _load_findings(raw):
    """--findings accepts an inline JSON array or a path to a JSON file."""
    if raw is None:
        return None
    p = Path(raw)
    if not raw.lstrip().startswith(("[", "{")) and p.is_file():
        raw = p.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise CascadeError(f"--findings is neither valid JSON nor a JSON file: {e}")


def _validate_findings(findings):
    """Each finding needs at least severity/location/claim (spec §5 QC path)."""
    if not isinstance(findings, list) or not findings:
        return "findings must be a non-empty array of objects"
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            return f"findings[{i}] must be an object"
        for key in FINDING_REQUIRED_KEYS:
            if not isinstance(f.get(key), str) or not f[key].strip():
                return f"findings[{i}].{key} must be a non-empty string"
    return None


def cmd_record_qc(args):
    try:
        state = _load_state(args.workspace)
    except CascadeError as e:
        _error(str(e))
        return EXIT_INVALID
    workspace = Path(state["workspace"])

    task = _find_task(state, args.task)
    if task is None:
        return _refuse("unknown_task", task_id=args.task)
    if task["status"] in TERMINAL_TASK_STATUSES:
        return _refuse("task_status_not_reviewable",
                       task_id=task["task_id"], status=task["status"])

    cap = QC_CAP[task["criticality"]]
    if task["qc_reviews"] >= cap:
        task["status"] = "failed"
        task["failure_reason"] = "qc_cap_exhausted"
        _save_state(workspace, state)
        return _refuse("qc_cap_exhausted", task_id=task["task_id"],
                       qc_reviews=task["qc_reviews"], cap=cap)

    try:
        findings = _load_findings(args.findings)
    except CascadeError as e:
        _error(str(e))
        return EXIT_INVALID

    if args.verdict == "reject":
        err = _validate_findings(findings)
        if err:
            _error("invalid_findings", detail=err)
            return EXIT_INVALID
    elif findings:
        # accept verdicts: open blocker/major findings block acceptance (§6)
        # unless explicitly dismissed on the record with --dismiss-reason (F8).
        blocking = [f for f in findings
                    if isinstance(f, dict)
                    and str(f.get("severity", "")).lower() in BLOCKING_SEVERITIES]
        if blocking and not (args.dismiss_reason and args.dismiss_reason.strip()):
            return _refuse("open_blocker_major_findings",
                           detail=("accept/accept-with-minor-fixes requires no open "
                                   "blocker/major findings, or --dismiss-reason to dismiss "
                                   "them on the record"))
        if blocking:
            dismissed = state.setdefault("dismissed_findings", [])
            for f in blocking:
                dismissed.append({
                    "timestamp": _now(),
                    "task_id": task["task_id"],
                    "finding": f,  # verbatim
                    "dismiss_reason": args.dismiss_reason,
                    "threat_model": state.get("threat_model"),
                })

    task["qc_reviews"] += 1
    state["counters"]["qc_reviews"] += 1
    if args.root_cause:
        task["root_cause"] = args.root_cause

    if args.verdict == "reject":
        task["status"] = "in_progress"
        task["evidence"].append({
            "timestamp": _now(),
            "rung": task["rung"],
            "executor": "qc",
            "status": "qc_reject",
            "detail": json.dumps(findings)[:MAX_OUTPUT_TAIL_CHARS],
            "child_session_id": None,
        })
    else:
        task["status"] = "done"
        task["qc_verdict"] = args.verdict
        if findings:
            task["qc_findings"] = findings

    _append_log(workspace, {
        "event": "record_qc",
        "task_id": task["task_id"],
        "verdict": args.verdict,
        "findings_count": len(findings) if isinstance(findings, list) else 0,
        "dismissed_count": len([f for f in (findings or []) if isinstance(f, dict)
                                and str(f.get("severity", "")).lower() in BLOCKING_SEVERITIES])
                           if args.verdict != "reject" and args.dismiss_reason else 0,
        "token_class": "qc",
        "cost_usd": None,
    })
    _save_state(workspace, state)
    _print_json({"recorded": True, "task_id": task["task_id"], "verdict": args.verdict,
                 "qc_reviews": task["qc_reviews"], "qc_cap": cap, "status": task["status"]})
    return EXIT_OK


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def cmd_verify(args):
    try:
        state = _load_state(args.workspace)
    except CascadeError as e:
        _error(str(e))
        return EXIT_INVALID
    workspace = Path(state["workspace"])

    task = _find_task(state, args.task)
    if task is None:
        return _refuse("unknown_task", task_id=args.task)

    # Verifier immutability (spec §6, load-bearing): ANY change to a frozen
    # verifier file -> automatic reject + mark for escalation, never accept.
    modified = []
    for rel in state["verifier_set"]:
        p = _safe_resolve(workspace, rel)
        if p is None or not p.is_file():
            modified.append({"path": rel, "change": "missing"})
            continue
        if _sha256_file(p) != state["verifier_hashes"].get(rel):
            modified.append({"path": rel, "change": "content"})
    if modified:
        task["force_escalate"] = True
        task["evidence"].append({
            "timestamp": _now(),
            "rung": task["rung"],
            "executor": "controller",
            "status": "verifier_modified",
            "detail": json.dumps(modified),
            "child_session_id": None,
        })
        _append_log(workspace, {
            "event": "verify",
            "task_id": task["task_id"],
            "verdict": "reject",
            "reason": "verifier_modified",
            "modified": modified,
            "token_class": "orchestration",
            "cost_usd": None,
        })
        _save_state(workspace, state)
        _print_json({"task_id": task["task_id"], "passed": False,
                     "reason": "verifier_modified", "modified": modified,
                     "action": "task marked for escalation; never accepted"})
        return EXIT_VERIFY_FAILED

    verification = task["verification"]
    det = verification.get("deterministic")
    if not (isinstance(det, str) and det.strip()):
        _print_json({"task_id": task["task_id"], "passed": None,
                     "reason": "no_deterministic_verifier",
                     "detail": "qc_review task — verifier-set immutability check passed"})
        return EXIT_OK

    # Run the deterministic verifier ourselves, shell=False, cwd=workspace,
    # bounded output. Worker-reported test output is never trusted (§6).
    try:
        argv = shlex.split(det)
    except ValueError as e:
        _error("verifier_command_unparseable", detail=str(e))
        return EXIT_INVALID
    if not argv:
        _error("verifier_command_empty")
        return EXIT_INVALID

    timed_out = False
    with tempfile.TemporaryFile() as tf:
        try:
            proc = subprocess.run(argv, cwd=str(workspace), stdout=tf,
                                  stderr=subprocess.STDOUT, timeout=args.timeout,
                                  shell=False)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            rc = None
            timed_out = True
        except OSError as e:
            _error("verifier_launch_failed", detail=f"{type(e).__name__}: {e}")
            return EXIT_INFRA
        tf.seek(0, os.SEEK_END)
        size = tf.tell()
        tf.seek(max(0, size - VERIFY_OUTPUT_TAIL_BYTES))
        tail = tf.read().decode("utf-8", errors="replace")

    passed = (rc == 0) and not timed_out
    result = {
        "ran_at": _now(),
        "command": det,
        "exit_code": rc,
        "timed_out": timed_out,
        "passed": passed,
        "output_tail": tail[-MAX_OUTPUT_TAIL_CHARS:],
    }
    task["verify_result"] = result
    if passed:
        task["verification_passed"] = True
    else:
        task["verification_passed"] = False
        task["evidence"].append({
            "timestamp": _now(),
            "rung": task["rung"],
            "executor": "controller",
            "status": "verify_failed",
            "detail": tail[-MAX_OUTPUT_TAIL_CHARS:],
            "child_session_id": None,
        })
        # Verifier-suspect escape hatch (spec §5): the SAME failure text across
        # two different executors opens a verifier-defect state.
        sig = hashlib.sha256(f"{rc}\n{tail}".encode("utf-8")).hexdigest()
        sigs = task.setdefault("failure_signatures", [])
        if not any(s["signature"] == sig and s["rung"] == task["rung"] for s in sigs):
            sigs.append({"rung": task["rung"], "signature": sig})
        rungs_with_sig = {s["rung"] for s in sigs if s["signature"] == sig}
        if len(rungs_with_sig) >= 2:
            state["verifier_defect_suspect"] = True

    _append_log(workspace, {
        "event": "verify",
        "task_id": task["task_id"],
        "verdict": "pass" if passed else "fail",
        "exit_code": rc,
        "timed_out": timed_out,
        "token_class": "orchestration",
        "cost_usd": None,
    })
    _save_state(workspace, state)
    _print_json({"task_id": task["task_id"], "passed": passed,
                 "exit_code": rc, "timed_out": timed_out,
                 "output_tail": result["output_tail"],
                 "verifier_defect_suspect": state["verifier_defect_suspect"]})
    return EXIT_OK if passed else EXIT_VERIFY_FAILED


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args):
    try:
        state = _load_state(args.workspace)
    except CascadeError as e:
        _error(str(e))
        return EXIT_INVALID

    tasks = state["tasks"]
    done = sum(1 for t in tasks if t["status"] == "done")
    cost_used = float(state.get("cost_used_usd", 0.0))
    ceiling = float(state["cost_ceiling_usd"])
    open_tasks = []
    for t in tasks:
        if t["status"] not in TERMINAL_TASK_STATUSES:
            rung = min(t["rung"], RUNG_K3_POST_ADVISOR)
            executor, profile = _executor_for_rung(t, rung, "codex")
            open_tasks.append({
                "task_id": t["task_id"],
                "status": t["status"],
                "rung": t["rung"],
                "rung_name": RUNG_NAMES[rung],
                "executor": executor,
                "attempts": t["attempts"],
                "attempt_cap": EXECUTOR_CAP[t["criticality"]],
                "qc_reviews": t["qc_reviews"],
            })
    _print_json({
        "goal": state["goal"],
        "threat_model": state.get("threat_model"),
        "tasks_done": done,
        "tasks_total": len(tasks),
        "counters": state["counters"],
        "cost_used_usd": cost_used,
        "cost_ceiling_usd": ceiling,
        "cost_warning_usd": state["cost_warning_usd"],
        "cost_warning": cost_used >= float(state["cost_warning_usd"]),
        "cost_exceeded": cost_used >= ceiling,
        "verifier_defect_suspect": state.get("verifier_defect_suspect", False),
        "dismissed_findings": state.get("dismissed_findings", []),
        "open_tasks": open_tasks,
    })
    return EXIT_OK


# ---------------------------------------------------------------------------
# replan
# ---------------------------------------------------------------------------

def cmd_replan(args):
    try:
        state = _load_state(args.workspace)
    except CascadeError as e:
        _error(str(e))
        return EXIT_INVALID
    workspace = Path(state["workspace"])

    if not args.confirm:
        return _refuse("replan_requires_confirm",
                       detail="replan replaces the task list; re-run with --confirm")
    counters = state["counters"]
    if counters.get("planner_calls", 0) + counters.get("replan_calls", 0) >= PLANNER_REPLAN_CAP:
        return _refuse("planner_replan_cap_exhausted",
                       planner_calls=counters.get("planner_calls"),
                       replan_calls=counters.get("replan_calls"),
                       cap=PLANNER_REPLAN_CAP)
    same_cause = [t["task_id"] for t in state["tasks"]
                  if t["status"] == "failed" and t.get("root_cause") == args.root_cause]
    if len(same_cause) < 2:
        return _refuse("replan_precondition_not_met",
                       detail=("replan requires >=2 tasks failed with the same root-cause "
                               f"marker {args.root_cause!r}; found {len(same_cause)}"),
                       matched=same_cause)

    try:
        plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    except OSError as e:
        _error("plan_file_unreadable", detail=str(e))
        return EXIT_INVALID
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        _error("plan_file_invalid_json", detail=str(e))
        return EXIT_INVALID

    errors = validate_plan(plan, workspace, _load_agent_names())
    if errors:
        _error("plan_validation_failed", details=errors)
        return EXIT_INVALID

    # Replace the task list and re-freeze the verifier set; keep goal, counters,
    # and cost accounting (replan calls DO count toward the cost ceiling, §5).
    state["tasks"] = [_new_task_entry(t) for t in plan["tasks"]]
    state["verifier_set"] = list(plan["verifier_set"])
    state["verifier_hashes"] = {
        v: _sha256_file(_safe_resolve(workspace, v)) for v in plan["verifier_set"]
    }
    state["verifier_defect_suspect"] = False
    counters["replan_calls"] = counters.get("replan_calls", 0) + 1

    _append_log(workspace, {
        "event": "replan",
        "token_class": "planning",
        "reason": args.reason,
        "root_cause": args.root_cause,
        "replaced_tasks": same_cause,
        "new_tasks": len(state["tasks"]),
        "cost_usd": None,
    })
    _save_state(workspace, state)
    _print_json({"replanned": True, "tasks": len(state["tasks"]),
                 "replan_calls": counters["replan_calls"],
                 "planner_replan_cap": PLANNER_REPLAN_CAP})
    return EXIT_OK


# ---------------------------------------------------------------------------
# commit-green
# ---------------------------------------------------------------------------

def cmd_commit_green(args):
    try:
        state = _load_state(args.workspace)
    except CascadeError as e:
        _error(str(e))
        return EXIT_INVALID
    workspace = Path(state["workspace"])

    task = _find_task(state, args.task)
    if task is None:
        return _refuse("unknown_task", task_id=args.task)
    # Only controller-run green counts (F6): never worker-reported success,
    # never a qc_review-only acceptance (verification_passed stays False there).
    if not task.get("verification_passed"):
        return _refuse("verify_not_passed",
                       task_id=task["task_id"],
                       detail="commit-green requires a controller-run verify pass for this task")
    scope = task.get("scope") or []
    if not scope:
        return _refuse("empty_scope", task_id=task["task_id"])
    if not _git_has_head(workspace):
        return _refuse("not_a_git_repo", task_id=task["task_id"])

    try:
        rc, _, err = _git_run(workspace, ["add", "--", *scope])
        if rc != 0:
            _error("git_add_failed", detail=err.strip()[-MAX_OUTPUT_TAIL_CHARS:])
            return EXIT_INFRA
        rc, _, _ = _git_run(workspace, ["diff", "--cached", "--quiet", "--", *scope])
        if rc == 0:
            return _refuse("nothing_to_commit", task_id=task["task_id"])
        message = (f"cascade: task {task['task_id']} verified green\n\n"
                   f"Verifier run by the controller: {task['verification'].get('deterministic')}")
        rc, out, err = _git_run(workspace, ["commit", "-m", message], timeout=60)
        if rc != 0:
            _error("git_commit_failed", detail=(err or out).strip()[-MAX_OUTPUT_TAIL_CHARS:])
            return EXIT_INFRA
        rc, rev, _ = _git_run(workspace, ["rev-parse", "HEAD"])
        commit_ref = rev.strip() if rc == 0 else None
    except (OSError, subprocess.TimeoutExpired) as e:
        _error("git_invocation_failed", detail=f"{type(e).__name__}: {e}")
        return EXIT_INFRA

    task["evidence"].append({
        "timestamp": _now(),
        "rung": task["rung"],
        "executor": "controller",
        "status": "commit_green",
        "detail": f"committed scope paths at {commit_ref}",
        "child_session_id": None,
    })
    _append_log(workspace, {
        "event": "commit_green",
        "task_id": task["task_id"],
        "commit_ref": commit_ref,
        "scope": scope,
        "token_class": "orchestration",
        "cost_usd": None,
    })
    _save_state(workspace, state)
    _print_json({"committed": True, "task_id": task["task_id"],
                 "commit_ref": commit_ref, "scope": scope})
    return EXIT_OK


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

def _last_dispatch_evidence(task):
    """The most recent evidence entry produced by a dispatch (executor or
    native K3), skipping controller/qc bookkeeping entries."""
    for ev in reversed(task.get("evidence", [])):
        if ev.get("executor") not in ("qc", "controller"):
            return ev
    return None


def cmd_rollback(args):
    try:
        state = _load_state(args.workspace)
    except CascadeError as e:
        _error(str(e))
        return EXIT_INVALID
    workspace = Path(state["workspace"])

    task = _find_task(state, args.task)
    if task is None:
        return _refuse("unknown_task", task_id=args.task)

    # Legal only when the task's last dispatch is in a terminal envelope state
    # (F7): a native_dispatch entry means a K3 worker may still be in flight.
    last = _last_dispatch_evidence(task)
    if last is None:
        return _refuse("no_dispatch_recorded", task_id=task["task_id"],
                       detail="nothing to roll back — no dispatch evidence")
    if last.get("status") not in TERMINAL_ENVELOPE_STATUSES:
        return _refuse("last_dispatch_not_terminal",
                       task_id=task["task_id"], last_status=last.get("status"),
                       detail="rollback is legal only when the last dispatch is "
                              "completed/failed/timeout")
    ref = task.get("checkpoint_ref")
    if not ref:
        return _refuse("no_checkpoint_ref", task_id=task["task_id"],
                       detail="the task has no git checkpoint (workspace may not be a repo)")
    scope = task.get("scope") or []
    if not scope:
        return _refuse("empty_scope", task_id=task["task_id"])

    try:
        rc, _, err = _git_run(workspace, ["checkout", ref, "--", *scope], timeout=60)
        if rc != 0:
            _error("git_checkout_failed", detail=err.strip()[-MAX_OUTPUT_TAIL_CHARS:])
            return EXIT_INFRA
    except (OSError, subprocess.TimeoutExpired) as e:
        _error("git_invocation_failed", detail=f"{type(e).__name__}: {e}")
        return EXIT_INFRA

    # checkout restores tracked files only; worker-created untracked files are
    # reported so the leader can remove them deliberately.
    untracked_left = []
    fc = last.get("files_changed")
    if isinstance(fc, dict):
        untracked_left = [f for f in fc.get("untracked", []) if isinstance(f, str)]

    task["evidence"].append({
        "timestamp": _now(),
        "rung": task["rung"],
        "executor": "controller",
        "status": "rollback",
        "detail": f"restored scope paths from {ref}",
        "child_session_id": None,
    })
    _append_log(workspace, {
        "event": "rollback",
        "task_id": task["task_id"],
        "checkpoint_ref": ref,
        "scope": scope,
        "untracked_left_in_place": untracked_left,
        "token_class": "orchestration",
        "cost_usd": None,
    })
    _save_state(workspace, state)
    _print_json({"rolled_back": True, "task_id": task["task_id"],
                 "checkpoint_ref": ref, "scope": scope,
                 "untracked_left_in_place": untracked_left})
    return EXIT_OK


# ---------------------------------------------------------------------------
# handoff
# ---------------------------------------------------------------------------

def cmd_handoff(args):
    """Bounded bootstrap packet for session resumes (F9).

    Deterministic: generated from state only (no wall clock), so identical
    state produces an identical packet. Hard cap HANDOFF_MAX_BYTES — oldest
    decisions are dropped first (count recorded), then the text is truncated.
    """
    try:
        state = _load_state(args.workspace)
    except CascadeError as e:
        _error(str(e))
        return EXIT_INVALID
    workspace = Path(state["workspace"])

    packet = {
        "goal": state["goal"],
        "created_at": state.get("created_at"),
        "threat_model": state.get("threat_model"),
        "workspace": state.get("workspace"),
        "counters": state["counters"],
        "cost_used_usd": state.get("cost_used_usd", 0.0),
        "cost_ceiling_usd": state["cost_ceiling_usd"],
        "tasks": [{
            "task_id": t["task_id"],
            "status": t["status"],
            "rung": t["rung"],
            "rung_name": RUNG_NAMES[min(t["rung"], RUNG_K3_POST_ADVISOR)],
            "attempts": t["attempts"],
            "qc_reviews": t["qc_reviews"],
            "verification_passed": t.get("verification_passed", False),
            "failure_reason": t.get("failure_reason"),
        } for t in state["tasks"]],
        "decisions": state.get("decisions", []),
        "dismissed_findings": state.get("dismissed_findings", []),
        "evidence_dir": str(_cascade_dir(workspace) / EVIDENCE_DIR_NAME),
    }

    text = json.dumps(packet, indent=2)
    dropped = 0
    while len(text.encode("utf-8")) > HANDOFF_MAX_BYTES and packet["decisions"]:
        packet["decisions"] = packet["decisions"][1:]  # drop oldest first
        dropped += 1
        text = json.dumps(packet, indent=2)
    if dropped:
        packet["decisions_dropped_oldest"] = dropped
        text = json.dumps(packet, indent=2)
    if len(text.encode("utf-8")) > HANDOFF_MAX_BYTES:
        text = text[:HANDOFF_MAX_BYTES - 48] + "\n...TRUNCATED (8KB cap)\n"
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return EXIT_OK


# ---------------------------------------------------------------------------
# record-decision
# ---------------------------------------------------------------------------

def cmd_record_decision(args):
    try:
        state = _load_state(args.workspace)
    except CascadeError as e:
        _error(str(e))
        return EXIT_INVALID
    workspace = Path(state["workspace"])

    if not args.decision.strip() or not args.rationale.strip():
        _error("invalid_decision", detail="--decision and --rationale must be non-empty")
        return EXIT_INVALID

    entry = {
        "timestamp": _now(),
        "decision": args.decision,
        "rationale": args.rationale,
        "rejected_alternatives": list(args.rejected or []),
        "source": args.source,
    }
    state.setdefault("decisions", []).append(entry)
    _append_log(workspace, {
        "event": "record_decision",
        "decision": args.decision,
        "source": args.source,
        "token_class": "orchestration",
        "cost_usd": None,
    })
    _save_state(workspace, state)
    _print_json({"recorded": True, "decisions": len(state["decisions"])})
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="cascade",
        description="Deterministic controller for the static model cascade (policy/STATIC_CASCADE_SPEC.md).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Validate plan, snapshot verifiers, write state")
    p.add_argument("--workspace", default=".")
    p.add_argument("--plan-file", required=True)
    p.add_argument("--threat-model", required=True, choices=THREAT_MODELS,
                   help="owner-set threat model for the goal; immutable once recorded")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("dispatch", help="Legality-checked dispatch of one task")
    p.add_argument("--workspace", default=".")
    p.add_argument("--task", required=True, help="task_id from the plan")
    p.add_argument("--reason", default=None, help="trigger note for the log")
    p.add_argument("--timeout", type=float, default=None, help="delegate timeout seconds")
    p.add_argument("--advisor", choices=sorted(ADVISOR_PROFILES), default="codex",
                   help="advisor profile to use when the ladder reaches the advisor rung")
    p.add_argument("--escalate", action="store_true",
                   help="force one escalation step before dispatching")
    p.set_defaults(func=cmd_dispatch)

    p = sub.add_parser("record-qc", help="Apply a QC verdict under the QC cap")
    p.add_argument("--workspace", default=".")
    p.add_argument("--task", required=True)
    p.add_argument("--verdict", required=True,
                   choices=["accept", "accept-with-minor-fixes", "reject"])
    p.add_argument("--findings", default=None,
                   help="inline JSON array or path to a JSON file; required for reject")
    p.add_argument("--root-cause", default=None,
                   help="root-cause marker stored on the task (used by replan)")
    p.add_argument("--dismiss-reason", default=None,
                   help="required to accept with open blocker/major findings; the "
                        "findings are stored verbatim in the goal's dismissed_findings")
    p.set_defaults(func=cmd_record_qc)

    p = sub.add_parser("verify", help="Verifier immutability check + run deterministic verifier")
    p.add_argument("--workspace", default=".")
    p.add_argument("--task", required=True)
    p.add_argument("--timeout", type=float, default=DEFAULT_VERIFY_TIMEOUT,
                   help="verifier command timeout seconds")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("status", help="One-line JSON status")
    p.add_argument("--workspace", default=".")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("replan", help="Replace the task list (requires --confirm)")
    p.add_argument("--workspace", default=".")
    p.add_argument("--plan-file", required=True)
    p.add_argument("--root-cause", required=True,
                   help="root-cause marker; >=2 failed tasks must carry it")
    p.add_argument("--reason", required=True)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_replan)

    p = sub.add_parser("commit-green",
                       help="git add + commit the task's scope paths (requires a "
                            "controller-run verify pass)")
    p.add_argument("--workspace", default=".")
    p.add_argument("--task", required=True)
    p.set_defaults(func=cmd_commit_green)

    p = sub.add_parser("rollback",
                       help="Scoped git checkout <checkpoint_ref> -- <scope paths>; "
                            "legal only when the last dispatch is terminal")
    p.add_argument("--workspace", default=".")
    p.add_argument("--task", required=True)
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser("handoff",
                       help="Bounded (8KB) bootstrap packet for session resumes")
    p.add_argument("--workspace", default=".")
    p.set_defaults(func=cmd_handoff)

    p = sub.add_parser("record-decision",
                       help="Append to the goal's append-only decisions log")
    p.add_argument("--workspace", default=".")
    p.add_argument("--decision", required=True)
    p.add_argument("--rationale", required=True)
    p.add_argument("--rejected", action="append", default=None,
                   help="rejected alternative (repeatable)")
    p.add_argument("--source", required=True, choices=["user", "leader"])
    p.set_defaults(func=cmd_record_decision)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
