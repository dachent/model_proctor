#!/usr/bin/env python3
"""runner — thin task-owning dispatch runner (MVP-001, issue #27).

A minimal deterministic control plane: task intake with observable features,
frozen task-start lane selection, worker dispatch through the delegate wrapper
(transport only), leader-executed verification, tree-bound acceptance receipts,
stagnation detection, and an append-only task record.

Designs out the frozen cascade's trust-boundary defects at birth:
  #17  receipts bind a tree signature; any mutation after verify stales them
  #18  a config-surface manifest (conftest.py, pytest.ini, ...) is hashed at
       init; new verification-affecting files reject verification
  #19  verifier commands are argv arrays — no POSIX shlex on Windows paths
  #20  a workspace inside a parent git repo is refused unless it IS the root

Sealed trust boundary (TOOL-013/TOOL-014): worker CLIs run in an isolated,
seeded KIMI_CODE_HOME (delegate side); runner state, receipts, the task
ledger, and sealed verifier payloads live OUTSIDE the agent-writable
workspace (default <ws_parent>/.runner-state/<ws>-<hash>, override with
--state-dir); at verify time, verification inputs whose content diverges
from the sealed copy are restored and the receipt flags the tamper
(restore-and-flag, ATIF class) while added/removed surface files reject.

Acceptance gate (TOOL-015/016/018). The gate consumes what verify records:
  - a receipt flagged tamper_detected is refused, not merely annotated;
  - a receipt is bound to the dispatch count it was written at, so
    verify -> dispatch -> accept can no longer accept the earlier green;
  - the verification contract (verifier argv, seal list) is pinned into
    external state at init, and verify refuses when the workspace task file
    diverges from the pin;
  - a workspace file shadowing a `-m` verifier module rejects verification
    (cmd_verify runs with cwd=ws, so the workspace is sys.path[0]);
  - the git tree signature hashes the content of every non-clean path, not
    just `git status` letters, which alone never staled an already-dirty
    file that was edited again.

Known residuals (#40). The state root is at a path the worker can compute
and write -- there is no OS-level confinement -- and the installed tool
directory is user-writable. `init --reinit` and the --state-dir boundary
check narrow the accidental cases; they do not make receipts unforgeable.
Treat the boundary as tamper-EVIDENT against a non-adversarial worker, not
sealed against a hostile one.

Stdlib only, Python 3.10, Windows-native.

Commands:
  lane     --task task.json                          print the frozen lane decision
  init     --workspace ws --task task.json           validate + seal + snapshot state
  dispatch --workspace ws --task task.json           run one worker attempt
  verify   --workspace ws --task task.json           leader-side acceptance check
  accept   --workspace ws --task task.json           refuse unless receipt fresh+green
  record   --workspace ws --task task.json [--wire wire.jsonl ...] [--pricing pricing.yaml]
  status   --workspace ws                            print runner state
  (stateful commands accept --state-dir to relocate the external state root)

Every command prints exactly one JSON object on stdout. Exit 0 = success,
1 = refused/failed, 2 = usage error, 3 = missing/bad config, 4 = internal.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCHEMA_VERSION = 1
STATE_DIR = ".runner"

# Lane ids. The lane table is FROZEN for the MVP experiment (#26 Phase 3 gate:
# no per-run discretionary relabeling).
LANES = ("flash", "glm", "k3")

# lane -> agent name in delegate's agents.json (roster names per README history).
# flash lane: ds-flash-worker (deepseek-v4-flash-0731 dated snapshot) — restored
# after the 0731 challenger arm beat gpt-oss-120b on the v3 corpus: 30/30 hidden
# vs 29/30, $0.0120 vs $0.0143 per hidden-pass (2026-08-25, #23).
DEFAULT_AGENT_MAP = {
    "flash": "ds-flash-worker",
    "glm": "glm-worker",
    "k3": "k3-worker",
}

# Files that can change what verification means without touching scope (#18).
CONFIG_SURFACE_NAMES = frozenset({
    "conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml",
    "sitecustomize.py", ".coveragerc", "Makefile",
})
CONFIG_SURFACE_GLOBS = ("*.pth",)

# Lateral switch map for execution stagnation (#26 failure-class table, MVP
# subset: provider failures switch lane class, not intelligence).
LATERAL_SWITCH = {"flash": "glm", "glm": "k3", "k3": "glm"}

DEFAULT_BUDGET = {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 1800}


# ── small utilities ─────────────────────────────────────────────────────

def _emit(obj, code=0):
    sys.stdout.write(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    return code


def _write_json_atomic(path, data):
    """fsync + os.replace; cleans up the temp file on any failure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_json(path, what):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(_emit({"error": f"{what} not found: {path}"}, 3))
    except json.JSONDecodeError as e:
        raise SystemExit(_emit({"error": f"{what} is not valid JSON: {e}"}, 3))


def _sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path):
    return _sha256_bytes(Path(path).read_bytes())


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


# ── task + lane ─────────────────────────────────────────────────────────

def load_task(path):
    task = _load_json(path, "task file")
    missing = [k for k in ("task_id", "prompt", "scope", "verifier") if k not in task]
    if missing:
        raise SystemExit(_emit({"error": f"task missing required fields: {missing}"}, 3))
    if not isinstance(task["scope"], list) or not task["scope"]:
        raise SystemExit(_emit({"error": "task.scope must be a non-empty list"}, 3))
    argv = task["verifier"].get("argv")
    if not isinstance(argv, list) or not argv:
        # Deliberately no shell-string form: argv arrays only (#19).
        raise SystemExit(_emit({"error": "task.verifier.argv must be a non-empty array"}, 3))
    task.setdefault("features", {})
    task.setdefault("budget", dict(DEFAULT_BUDGET))
    for k, v in DEFAULT_BUDGET.items():
        task["budget"].setdefault(k, v)
    return task


def lane_for(features):
    """Frozen task-start lane table (#26 Increment 2). Returns (lane, reasons)."""
    if features.get("open_ended") or features.get("marathon"):
        return "k3", ["open-ended/marathon task shape -> K3 task-owning worker"]
    if features.get("multi_module") or features.get("unfamiliar_repo"):
        return "glm", ["substantial multi-module/unfamiliar-repo work -> GLM worker"]
    if (features.get("bounded") and features.get("known_location")
            and features.get("objective_acceptance")):
        return "flash", ["localized + bounded + objective acceptance -> Flash worker"]
    return "glm", ["default: substantial work without a bounded signature -> GLM worker"]


# ── production-runner guard ─────────────────────────────────────────────
# Incident class (fsn_rpt_wk_finops week-run 2026-08-25): a task driving a
# production pipeline orchestrator was feature-declared "bounded +
# known_location" and dispatched to the flash lane; worker #1 burned its whole
# budget discovering environment prerequisites (the WKFINOPS-231 durable
# checkout) that live statically in the orchestrator's own preflight surface.
# Deterministic rule: tasks that drive production runners are never flash —
# regardless of declared features — and their leader must attach preflight
# receipts before any dispatch.

PRODUCTION_RUNNER_PATTERNS = (
    "run_week.ps1",
    "src.run_all",
    "src.run_weekly",
    "run_readiness_doctor",
    "morning_battery",
)


def production_guard(task):
    """Known-entrypoint denylist over leader-authored task text.

    Deterministic *given the text* — but the text is the same surface that
    mis-declared features in the originating incident, so a prompt that never
    names a known entrypoint ("run the weekly pipeline") evades this entirely.
    It hardens recurrences of a known incident; it is not a general ops-class
    detector. Returns (is_production, matched).
    """
    hay = "\n".join([
        str(task.get("prompt", "")),
        json.dumps(task.get("scope", [])),
        json.dumps(task.get("verifier", {})),
    ]).lower()
    matched = sorted({p for p in PRODUCTION_RUNNER_PATTERNS if p.lower() in hay})
    return bool(matched), matched


def check_production_guard(task, lane):
    """Refuse illegal production/lane combinations at init time.

    flash is forbidden for production-runner tasks unless the leader set an
    explicit ``lane`` in the task file (the skill's documented override path —
    an explicit override is a reviewed decision, not feature guesswork).
    Returns an advisory dict for state/output, or None for non-production.
    """
    is_prod, matched = production_guard(task)
    if not is_prod:
        return None
    if lane == "flash" and not task.get("lane"):
        raise SystemExit(_emit({
            "error": "flash_lane_forbidden_production_runner",
            "detail": (
                "task drives production pipeline runners; the flash lane is "
                "ineligible (multi-hour supervised ops work). Set an explicit "
                "`lane` override with justification in the task file, or let "
                "the features classify honestly."
            ),
            "matched_patterns": matched,
        }, 1))
    return {"production_task": True, "matched_patterns": matched}


# ── workspace surface / tree signatures ──────────────────────────────────

def _is_config_surface(relpath):
    name = os.path.basename(relpath)
    if name in CONFIG_SURFACE_NAMES:
        return True
    return any(Path(name).match(g) for g in CONFIG_SURFACE_GLOBS)


def _walk_files(ws):
    for root, dirs, files in os.walk(ws):
        dirs[:] = [d for d in dirs if d not in (STATE_DIR, ".git", "__pycache__")]
        for f in sorted(files):
            p = os.path.join(root, f)
            yield os.path.relpath(p, ws).replace(os.sep, "/"), p


def config_surface(ws):
    """{relpath: sha256} of every verification-affecting config file (#18)."""
    return {rel: _sha256_file(p) for rel, p in _walk_files(ws) if _is_config_surface(rel)}


def _git_toplevel(ws):
    try:
        r = subprocess.run(
            ["git", "-C", str(ws), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def check_workspace_root(ws):
    """#20: refuse a nested workspace that would bind to a parent repo."""
    top = _git_toplevel(ws)
    if top is None:
        return  # not a git repo at all: fine, file-manifest signatures are used
    if _norm(top) != _norm(ws):
        raise SystemExit(_emit({
            "error": "workspace_is_not_repo_root",
            "detail": f"workspace {ws} resolves to enclosing repo {top}; "
                      f"refusing to bind runner state to a parent repository",
        }, 1))


def _porcelain_entries(ws):
    """[(xy, relpath)] parsed from -z porcelain v1. Fails closed.

    -z is required: with core.quotePath on (the default) the newline-delimited
    form C-quotes any path containing non-ASCII, quotes, or backslashes, and a
    naive parser corrupts those. Rename/copy records carry a second
    origin-path field that must be consumed, not read as another entry.
    """
    r = subprocess.run(
        ["git", "-C", str(ws), "status", "--porcelain=v1", "-z",
         "--untracked-files=all"],
        capture_output=True, timeout=60)
    if r.returncode != 0:
        raise SystemExit(_emit({
            "error": "git_status_failed",
            "detail": "cannot compute a tree signature; refusing rather than "
                      "signing empty output",
            "stderr": r.stderr.decode("utf-8", "replace")[-400:],
        }, 1))
    fields = r.stdout.decode("utf-8", "surrogateescape").split("\0")
    entries, i = [], 0
    while i < len(fields):
        f = fields[i]
        if not f or len(f) < 4:
            i += 1
            continue
        xy = f[:2]
        entries.append((xy, f[3:]))
        if "R" in xy or "C" in xy:
            i += 1          # consume the origin path of a rename/copy record
        i += 1
    return entries


def tree_signature(ws):
    """Signature of the exact tree a verifier ran against.

    Git workspace: HEAD + the non-clean entry set + the sha256 of each
    non-clean path + the config surface.
    Non-git workspace: hash of the sorted (relpath, sha256) manifest.
    Either way, ANY mutation after verify produces a different signature (#17).
    """
    if _git_toplevel(ws) is not None:
        head = subprocess.run(["git", "-C", str(ws), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30)
        # An unborn branch (git init, no commit) legitimately has no HEAD;
        # mark it explicitly rather than signing empty output.
        head_part = head.stdout.strip() if head.returncode == 0 else "<unborn>"
        rows = []
        for xy, rel in _porcelain_entries(ws):
            rows.append(f"{xy} {rel}")
            # Porcelain reports status letters and paths, never content: a
            # file already reading ' M path' keeps a byte-identical line when
            # re-edited, so HEAD + status alone does NOT stale a receipt on
            # the very mutation class this signature exists to catch. Hash
            # the non-clean set — small after a dispatch, unlike the tree.
            fp = Path(ws) / rel
            if fp.is_file():
                try:
                    rows.append("  " + _sha256_file(fp))
                except OSError:
                    rows.append("  <unreadable>")
            elif fp.is_dir():
                rows.append("  <dir>")
            else:
                rows.append("  <absent>")
        # The config surface is walked directly rather than through git, so
        # verification-affecting files hidden by .gitignore are bound here.
        for rel, digest in sorted(config_surface(ws).items()):
            rows.append(f"cfg {digest}  {rel}")
        payload = (head_part + "\n" + "\n".join(rows)).encode("utf-8")
        return "git:" + _sha256_bytes(payload)
    rows = []
    for rel, p in _walk_files(ws):
        rows.append(f"{_sha256_file(p)}  {rel}")
    return "files:" + _sha256_bytes("\n".join(sorted(rows)).encode("utf-8"))


def _verifier_modules(argv):
    """Module names a `-m` verifier would import (`-m x` and `-mx` forms)."""
    mods = []
    for i, a in enumerate(argv):
        if a == "-m" and i + 1 < len(argv):
            mods.append(argv[i + 1])
        elif a.startswith("-m") and len(a) > 2 and not a.startswith("--"):
            mods.append(a[2:])
    return mods


def module_shadow_check(task, ws, probe_cwd):
    """Workspace files that would hijack a `-m MOD` verifier.

    `python -m MOD` puts the process cwd at sys.path[0], and cmd_verify runs
    the verifier with cwd=ws — so ws/MOD.py or ws/MOD/__init__.py resolves
    ahead of the real module. A one-line ws/unittest.py turns a failing suite
    into exit 0. Neither name is in CONFIG_SURFACE_NAMES, and a `-m` argument
    is not a workspace file, so seal_files() never covers it and the
    added/removed surface check never fires.

    Only a candidate that ALSO resolves outside the workspace counts: a module
    that exists only in the tree is the verifier's intended target, not a
    shadow. Returns a sorted list of shadowing relpaths.
    """
    shadowed = []
    for mod in _verifier_modules(task["verifier"]["argv"]):
        top = mod.split(".")[0]
        local = [c for c in (f"{top}.py", f"{top}/__init__.py")
                 if (Path(ws) / c).is_file()]
        if not local:
            continue
        probe = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util as u, sys;"
             "sys.exit(0 if u.find_spec(sys.argv[1]) else 1)", top],
            capture_output=True, cwd=str(probe_cwd), timeout=60)
        if probe.returncode == 0:
            shadowed.extend(local)
    return sorted(set(shadowed))


# ── state (TOOL-014: OUTSIDE the agent-writable workspace) ──────────────

def _state_root(ws, state_dir=None):
    """Runner state root. Receipts, state, the ledger, and sealed verifier
    payloads must not be rewritable by the worker they judge — the default is
    a sibling of the workspace, not a directory inside it."""
    if state_dir:
        root = Path(state_dir).expanduser().resolve()
        wsr = Path(ws).resolve()
        if root == wsr or wsr in root.parents:
            raise SystemExit(_emit({
                "error": "state_dir_inside_workspace",
                "detail": "runner state must not be writable by the worker it "
                          "judges; choose a path outside the workspace. Note "
                          "that STATE_DIR is pruned by _walk_files, so an "
                          "in-workspace state root would also be invisible to "
                          "config_surface() and the files: tree signature.",
                "state_dir": str(root),
                "workspace": str(wsr),
            }, 1))
        return root
    ws_res = Path(ws).resolve()
    key = re.sub(r"[^A-Za-z0-9_.-]+", "_", ws_res.name)
    digest = hashlib.sha256(str(ws_res).encode("utf-8")).hexdigest()[:8]
    return ws_res.parent / ".runner-state" / f"{key}-{digest}"


def _state_path(root):
    return Path(root) / "state.json"


def _receipt_path(root, task_id):
    return Path(root) / f"receipt-{task_id}.json"


def _load_state(root):
    p = _state_path(root)
    if not p.is_file():
        raise SystemExit(_emit({"error": "runner not initialized; run `init` first"}, 3))
    return _load_json(p, "runner state")


def seal_files(task, ws, sroot):
    """Copy the verification payload out of the agent-writable workspace at
    init: task["seal"] entries + verifier argv file args + every config-surface
    file. Returns {relpath: sha256} of what was sealed."""
    files = set(task.get("seal", []))
    for a in task["verifier"]["argv"]:
        if a.startswith("{") or Path(a).is_absolute():
            continue
        if (Path(ws) / a).is_file():
            files.add(a.replace(os.sep, "/"))
    files |= set(config_surface(ws).keys())
    sealed = {}
    for rel in sorted(files):
        src = Path(ws) / rel
        if not src.is_file():
            continue
        dst = Path(sroot) / "sealed" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        sealed[rel] = _sha256_file(src)
    return sealed


# ── failure fingerprints / stagnation ───────────────────────────────────

def normalize_failure(text):
    """Normalize verifier output into a stable failure fingerprint input."""
    tail = (text or "")[-400:]
    tail = tail.lower()
    tail = re.sub(r"[0-9]+(\.[0-9]+)?s\b", " ", tail)          # timings
    tail = re.sub(r"[\w./\\-]+[\\/]", " ", tail)               # paths
    tail = re.sub(r"\d+", "#", tail)                            # numbers
    tail = re.sub(r"\s+", " ", tail).strip()
    return _sha256_bytes(tail.encode("utf-8"))[:16]


def classify_and_recommend(state, lane):
    """#26 failure-class table, MVP subset. Returns (class, recommendation)."""
    failures = state.get("failures", [])
    if not failures:
        return None, None
    last = failures[-1]
    if last.get("kind") == "provider_or_tool":
        return "provider_or_tool", {
            "action": "switch_provider_or_harness",
            "note": "provider/tool failure is not an intelligence escalation",
        }
    stagnant = state.get("budget", DEFAULT_BUDGET)["max_stagnant"]
    recent = [f["fingerprint"] for f in failures[-stagnant:]]
    if len(recent) == stagnant and len(set(recent)) == 1:
        return "execution_stagnation", {
            "action": "lateral_switch",
            "to_lane": LATERAL_SWITCH[lane],
            "note": "restart from a compact evidence packet, not the failed rationale",
        }
    return "execution_failure", {"action": "retry_same", "lane": lane}


# ── delegate transport ──────────────────────────────────────────────────

def resolve_delegate(explicit):
    for cand in (explicit, os.environ.get("DELEGATE_PATH"),
                 r"C:\Tools\model-proctor\delegate.py"):
        if cand and Path(cand).is_file():
            return str(cand)
    raise SystemExit(_emit({
        "error": "delegate.py not found; pass --delegate or set DELEGATE_PATH"}, 3))


def run_delegate(delegate_py, agent, ws, prompt, timeout_s):
    """One worker attempt through the delegate wrapper. Returns the envelope."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(prompt)
        task_file = tf.name
    try:
        cmd = [sys.executable, delegate_py, "--agent", agent, "--workspace", str(ws),
               "--task-file", task_file, "--timeout", str(timeout_s)]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout_s + 120)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": "delegate_wrapper_timeout",
                "duration_seconds": timeout_s, "agent": agent}
    finally:
        try:
            os.unlink(task_file)
        except OSError:
            pass
    line = (r.stdout or "").strip().splitlines()
    if not line:
        return {"status": "internal_error", "error": "empty delegate output",
                "stderr": (r.stderr or "")[-500:], "agent": agent}
    try:
        return json.loads(line[-1])
    except json.JSONDecodeError:
        return {"status": "internal_error", "error": "unparseable delegate envelope",
                "stdout_tail": (r.stdout or "")[-500:], "agent": agent}


# ── commands ────────────────────────────────────────────────────────────

def cmd_lane(args):
    task = load_task(args.task)
    lane, reasons = lane_for(task["features"])
    out = {"task_id": task["task_id"], "lane": lane, "reasons": reasons}
    is_prod, matched = production_guard(task)
    if is_prod:
        out["production_task"] = True
        out["matched_patterns"] = matched
        if lane == "flash" and not task.get("lane"):
            out["guard"] = "flash_lane_forbidden_production_runner (init/dispatch will refuse)"
    return _emit(out)


def cmd_init(args):
    ws = str(Path(args.workspace).resolve())
    task = load_task(args.task)
    check_workspace_root(ws)
    sroot = _state_root(ws, args.state_dir)
    # Re-running init unconditionally re-sealed whatever was on disk NOW,
    # re-snapshotted the config surface, and reset dispatches/failures/accepted
    # -- so a worker never needed to forge a receipt, only to run `init`.
    # Re-initialisation is sometimes legitimate, so require the intent to be
    # stated and count it on state, where the leader can see it.
    prior = _state_path(sroot)
    reinit_count = 0
    if prior.is_file():
        if not getattr(args, "reinit", False):
            raise SystemExit(_emit({
                "error": "already_initialized",
                "detail": "state already exists for this workspace; re-running "
                          "init would re-seal the CURRENT tree as the baseline "
                          "and reset the dispatch budget. Pass --reinit if that "
                          "is what you intend.",
                "state_dir": str(sroot),
            }, 1))
        try:
            reinit_count = int(_load_json(prior, "runner state")
                               .get("reinit_count", 0)) + 1
        except (ValueError, TypeError):
            reinit_count = 1
    lane = task.get("lane") or lane_for(task["features"])[0]
    if lane not in LANES:
        raise SystemExit(_emit({"error": f"unknown lane: {lane}"}, 3))
    guard = check_production_guard(task, lane)
    sealed = seal_files(task, ws, sroot)
    state = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task["task_id"],
        "workspace": ws,
        "lane": lane,
        # A reviewed decision, recorded outside the worker-writable tree so
        # dispatch can honour it without re-reading the task file.
        "lane_override": bool(task.get("lane")),
        "reinit_count": reinit_count,
        # The verification contract, pinned OUTSIDE the agent-writable tree.
        # task.json is re-read on every command and lives in the workspace, so
        # an unpinned argv lets a worker swap the exam for print('ok').
        "verifier": task["verifier"],
        "task_seal": sorted(task.get("seal", [])),
        "scope": task["scope"],
        "budget": task["budget"],
        "init_config_surface": config_surface(ws),
        "init_tree_sig": tree_signature(ws),
        "sealed": sealed,
        "dispatches": [],
        "failures": [],
        "accepted": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _write_json_atomic(_state_path(sroot), state)
    out = {"initialized": True, "task_id": task["task_id"], "lane": lane,
           "state_dir": str(sroot), "sealed_files": sorted(sealed),
           "reinit_count": reinit_count,
           "config_surface_files": sorted(state["init_config_surface"])}
    if guard:
        out["production_guard"] = guard
    return _emit(out)


def cmd_dispatch(args):
    ws = str(Path(args.workspace).resolve())
    task = load_task(args.task)
    sroot = _state_root(ws, args.state_dir)
    state = _load_state(sroot)
    if state["accepted"]:
        raise SystemExit(_emit({"error": "task already accepted"}, 1))
    if len(state["dispatches"]) >= state["budget"]["max_dispatches"]:
        raise SystemExit(_emit({"error": "dispatch budget exhausted",
                                "dispatches": len(state["dispatches"])}, 1))
    # Production-runner guard, enforced again at dispatch (defense in depth —
    # the task file may have changed since init).
    preflight_ages = None
    is_prod, matched = production_guard(task)
    if is_prod and state["lane"] == "flash" and not state.get("lane_override"):
        raise SystemExit(_emit({
            "error": "flash_lane_forbidden_production_runner",
            "detail": "state lane is flash for a production-runner task and no "
                      "explicit `lane` override was recorded at init; set an "
                      "explicit `lane` in the task file and re-init.",
            "matched_patterns": matched,
        }, 1))
    if is_prod:
        # Leader-side preflight mandate: the deterministic probe (doctor /
        # battery / dry-run) must have RUN before a worker is spent — discovery
        # is what probes are for, not what dispatch budgets are for. An absent
        # or empty receipt list refuses exactly like a missing file.
        receipts = task.get("preflight_receipts", [])
        if not isinstance(receipts, list):
            raise SystemExit(_emit({"error": "preflight_receipts must be a list of file paths"}, 3))
        missing = [p for p in receipts if not Path(str(p)).expanduser().is_file()]
        if not receipts or missing:
            raise SystemExit(_emit({
                "error": "preflight_receipt_required",
                "missing": missing,
                "hint": "run the orchestrator's own preflight (doctor/battery/"
                        "dry-run) and list the resulting log/report paths in "
                        "the task file's `preflight_receipts` array",
            }, 1))
        # Existence alone is satisfied by `touch`. These receipts are authored
        # by the leader — the trusted party here — so hashing them would
        # defend against nobody. What existence cannot catch is LAST WEEK's
        # receipt being replayed for today's run; age is the check with teeth.
        max_age = state["budget"].get("max_preflight_age_s", 86400)
        now = time.time()
        ages = {str(q): round(now - Path(str(q)).expanduser().stat().st_mtime)
                for q in receipts}
        stale = sorted(k for k, v in ages.items() if v > max_age)
        if stale:
            raise SystemExit(_emit({
                "error": "preflight_receipt_stale",
                "stale": stale,
                "ages_seconds": ages,
                "max_age_seconds": max_age,
                "hint": "re-run the preflight; these receipts predate the "
                        "allowed window (override with "
                        "budget.max_preflight_age_s)",
            }, 1))
        preflight_ages = ages
    agent_map = dict(DEFAULT_AGENT_MAP)
    if args.agent_map:
        agent_map.update(_load_json(args.agent_map, "agent map"))
    delegate_py = resolve_delegate(args.delegate)
    agent = agent_map[state["lane"]]
    t0 = time.monotonic()
    envelope = run_delegate(delegate_py, agent, ws, task["prompt"],
                            state["budget"]["timeout_s"])
    wall = time.monotonic() - t0
    envelope_status = envelope.get("status")
    state["dispatches"].append({
        "agent": agent, "status": envelope_status,
        "duration_seconds": envelope.get("duration_seconds", wall),
        "child_session_id": envelope.get("child_session_id"),
        "child_home": envelope.get("child_home"),
        # Which preflight evidence authorised this dispatch, and how old it
        # was — the check previously left no trace at all.
        "preflight_ages_seconds": preflight_ages,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    if envelope_status not in ("completed", "failed"):
        state["failures"].append({
            "kind": "provider_or_tool",
            "fingerprint": f"provider:{envelope_status}",
            "at": state["dispatches"][-1]["at"],
        })
    _write_json_atomic(_state_path(sroot), state)
    cls, rec = classify_and_recommend(state, state["lane"])
    return _emit({
        "dispatched": True, "agent": agent, "lane": state["lane"],
        "envelope_status": envelope_status,
        "child_session_id": envelope.get("child_session_id"),
        "child_home": envelope.get("child_home"),
        "failure_class": cls, "recommendation": rec,
    })


def cmd_verify(args):
    ws = str(Path(args.workspace).resolve())
    task = load_task(args.task)
    sroot = _state_root(ws, args.state_dir)
    state = _load_state(sroot)

    # #18: no NEW or REMOVED verification-affecting files since init.
    now_surface = config_surface(ws)
    added = sorted(set(now_surface) - set(state["init_config_surface"]))
    removed = sorted(set(state["init_config_surface"]) - set(now_surface))
    if added or removed:
        receipt = {"task_id": task["task_id"], "passed": False,
                   "rejected": "config_surface_changed",
                   "added": added, "removed": removed,
                   "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        _write_json_atomic(_receipt_path(sroot, task["task_id"]), receipt)
        return _emit(receipt, 1)

    # TOOL-014: restore any MODIFIED verification input from the sealed copy
    # (reset modified inputs before testing) and flag the tamper on the receipt.
    restored = []
    for rel, sealed_hash in state.get("sealed", {}).items():
        cur = Path(ws) / rel
        if not cur.is_file() or _sha256_file(cur) != sealed_hash:
            shutil.copy2(Path(sroot) / "sealed" / rel, cur)
            restored.append(rel)

    # B3: the pinned contract outranks the live task file.
    pinned = state.get("verifier")
    if pinned is not None and pinned != task["verifier"]:
        receipt = {"task_id": task["task_id"], "passed": False,
                   "rejected": "verifier_changed_since_init",
                   "pinned_argv": pinned.get("argv"),
                   "task_file_argv": task["verifier"].get("argv"),
                   "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        _write_json_atomic(_receipt_path(sroot, task["task_id"]), receipt)
        return _emit(receipt, 1)

    # B4: a pinned argv is still worthless if the module it names can be
    # resolved out of the workspace instead.
    shadowed = module_shadow_check(task, ws, sroot)
    if shadowed:
        receipt = {"task_id": task["task_id"], "passed": False,
                   "rejected": "module_shadow_detected",
                   "shadowed": shadowed,
                   "detail": "workspace files shadow a module the verifier "
                             "imports via -m; the workspace is sys.path[0]",
                   "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        _write_json_atomic(_receipt_path(sroot, task["task_id"]), receipt)
        return _emit(receipt, 1)

    argv = [sys.executable if a == "{python}" else a for a in task["verifier"]["argv"]]
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as tf:
        r = subprocess.run(argv, cwd=ws, stdout=tf, stderr=subprocess.STDOUT,
                           timeout=state["budget"]["timeout_s"])
        tf.seek(0)
        output = tf.read()
    passed = r.returncode == 0
    receipt = {
        "task_id": task["task_id"],
        "passed": passed,
        "verifier_exit": r.returncode,
        "verifier_output_hash": _sha256_bytes(output.encode("utf-8")),
        "verifier_restored": restored,
        "tamper_detected": bool(restored),
        # Binds the receipt to the dispatch it describes, so a later dispatch
        # cannot be accepted on an earlier green receipt.
        "dispatch_seq": len(state["dispatches"]),
        # A green receipt now says WHAT was verified, not only which tree.
        "verifier_argv": task["verifier"]["argv"],
        "tree_sig": tree_signature(ws),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _write_json_atomic(_receipt_path(sroot, task["task_id"]), receipt)
    if not passed:
        state["failures"].append({
            "kind": "execution",
            "fingerprint": normalize_failure(output),
            "at": receipt["at"],
        })
        _write_json_atomic(_state_path(sroot), state)
        cls, rec = classify_and_recommend(state, state["lane"])
        receipt["failure_class"] = cls
        receipt["recommendation"] = rec
    return _emit(receipt, 0 if passed else 1)


def cmd_accept(args):
    ws = str(Path(args.workspace).resolve())
    task = load_task(args.task)
    sroot = _state_root(ws, args.state_dir)
    state = _load_state(sroot)
    rp = _receipt_path(sroot, task["task_id"])
    if not rp.is_file():
        raise SystemExit(_emit({"accepted": False, "reason": "no receipt; run verify"}, 1))
    receipt = _load_json(rp, "receipt")
    if not receipt.get("passed"):
        raise SystemExit(_emit({"accepted": False, "reason": "receipt not green",
                                "receipt": receipt}, 1))
    # Restore-and-flag (TOOL-014) had no consumer: a receipt carrying
    # tamper_detected was accepted silently, which makes the whole ATIF design
    # decorative at the only point where it matters. A tampered verification
    # surface is not acceptable evidence. Recoverable, not terminal -- the
    # restore already made the files match the seal, so re-verifying yields a
    # clean receipt.
    if receipt.get("tamper_detected"):
        raise SystemExit(_emit({
            "accepted": False,
            "reason": "tamper_detected: verification inputs were restored at "
                      "verify; re-verify on the restored tree before accepting",
            "verifier_restored": receipt.get("verifier_restored", []),
            "receipt": receipt,
        }, 1))
    # A dispatch after a green verify left the receipt untouched, so
    # verify -> dispatch -> accept was gated by tree_sig alone. When the second
    # dispatch changes no bytes the signature does not move and the FIRST
    # dispatch's receipt authorises the accept -- issue #17's sticky-flag
    # defect. The receipt must describe the current dispatch count.
    seq = receipt.get("dispatch_seq")
    if seq is not None and seq != len(state["dispatches"]):
        raise SystemExit(_emit({
            "accepted": False,
            "reason": "stale_receipt: dispatches occurred after verification",
            "receipt_dispatch_seq": seq,
            "current_dispatches": len(state["dispatches"]),
        }, 1))
    current = tree_signature(ws)
    if current != receipt["tree_sig"]:
        # #17: the green receipt no longer describes this tree.
        raise SystemExit(_emit({
            "accepted": False,
            "reason": "stale_receipt: tree mutated after verification",
            "receipt_tree_sig": receipt["tree_sig"],
            "current_tree_sig": current,
        }, 1))
    state["accepted"] = True
    state["accepted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write_json_atomic(_state_path(sroot), state)
    return _emit({"accepted": True, "task_id": task["task_id"],
                  "receipt": receipt})


# ── metering (record) ───────────────────────────────────────────────────

def load_pricing(path):
    """Same inline-map format as evals/meter.py's load_pricing."""
    pricing = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^([\w/.-]+):\s*\{(.+)\}\s*$", stripped)
        if not m:
            continue
        fields = {}
        for part in m.group(2).split(","):
            kv = part.split(":")
            if len(kv) == 2:
                fields[kv[0].strip()] = float(kv[1].strip())
        if {"input", "cached_input", "output"} <= fields.keys():
            pricing[m.group(1)] = fields
    return pricing


def sum_usage_records(wire_path):
    """Sum usage.record events in one wire.jsonl (meter.py idiom: usage.record
    only — step.end also carries usage and would double-count)."""
    totals = {}
    records = 0
    for line in Path(wire_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("type") != "usage.record":
            continue
        model = evt.get("model") or "unknown"
        usage = evt.get("usage", {})
        records += 1
        bucket = totals.setdefault(model, {"inputOther": 0, "output": 0,
                                           "inputCacheRead": 0,
                                           "inputCacheCreation": 0})
        for k in bucket:
            bucket[k] += usage.get(k, 0)
    return records, totals


def price_tokens(totals, pricing):
    """USD per model: (inputOther + cacheCreation) at input price,
    cacheRead at cached price, output at output price."""
    by_model = {}
    for model, t in totals.items():
        p = pricing.get(model)
        if p is None:
            by_model[model] = None
            continue
        by_model[model] = round(
            (t["inputOther"] + t["inputCacheCreation"]) * p["input"] / 1e6
            + t["inputCacheRead"] * p["cached_input"] / 1e6
            + t["output"] * p["output"] / 1e6, 6)
    known = [v for v in by_model.values() if v is not None]
    return by_model, (round(sum(known), 6) if len(known) == len(by_model) else None)


def _receipt_flag(sroot, task_id):
    """tamper_detected from the task's receipt, or None when there is none."""
    rp = _receipt_path(sroot, task_id)
    if not rp.is_file():
        return None
    try:
        return bool(json.loads(rp.read_text(encoding="utf-8")).get("tamper_detected"))
    except (OSError, json.JSONDecodeError):
        return None


def cmd_record(args):
    ws = str(Path(args.workspace).resolve())
    task = load_task(args.task)
    sroot = _state_root(ws, args.state_dir)
    state = _load_state(sroot)
    row = {
        "task_id": task["task_id"],
        "lane": state["lane"],
        "dispatches": len(state["dispatches"]),
        "agents_used": sorted({d["agent"] for d in state["dispatches"]}),
        "accepted": state["accepted"],
        "tamper_detected": _receipt_flag(sroot, task["task_id"]),
        "failures": len(state["failures"]),
        "wall_time_seconds": round(sum(
            d.get("duration_seconds") or 0 for d in state["dispatches"]), 3),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if args.wire:
        records = 0
        totals = {}
        for wire in args.wire:
            n, t = sum_usage_records(wire)
            records += n
            for model, bucket in t.items():
                agg = totals.setdefault(model, {"inputOther": 0, "output": 0,
                                                "inputCacheRead": 0,
                                                "inputCacheCreation": 0})
                for k in agg:
                    agg[k] += bucket[k]
        row["usage_records"] = records
        row["tokens_by_model"] = totals
        if args.pricing:
            by_model, total = price_tokens(totals, load_pricing(args.pricing))
            row["cost_usd_by_model"] = by_model
            row["api_cost_usd"] = total
    out = Path(sroot) / "tasks.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return _emit({"recorded": True, "row": row, "log": str(out)})


def cmd_status(args):
    ws = str(Path(args.workspace).resolve())
    return _emit(_load_state(_state_root(ws, args.state_dir)))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="runner", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("lane", "init", "dispatch", "verify", "accept", "record", "status"):
        p = sub.add_parser(name)
        if name != "status":
            p.add_argument("--task", required=True)
        if name != "lane":
            p.add_argument("--workspace", required=True)
            p.add_argument("--state-dir", default=None,
                           help="override the external runner-state location "
                                "(default: sibling .runner-state/<ws>-<hash>)")
        if name == "init":
            p.add_argument("--reinit", action="store_true",
                           help="re-baseline an already-initialized workspace: "
                                "re-seals the CURRENT tree and resets the "
                                "dispatch budget (counted on state)")
        if name == "dispatch":
            p.add_argument("--delegate", default=None)
            p.add_argument("--agent-map", default=None)
        if name == "record":
            p.add_argument("--wire", nargs="+", default=None,
                           help="one or more wire.jsonl files for usage metering")
            p.add_argument("--pricing", default=None)
    args = parser.parse_args(argv)
    return {
        "lane": cmd_lane, "init": cmd_init, "dispatch": cmd_dispatch,
        "verify": cmd_verify, "accept": cmd_accept, "record": cmd_record,
        "status": cmd_status,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
