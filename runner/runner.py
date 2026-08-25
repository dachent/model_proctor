#!/usr/bin/env python3
"""runner — thin task-owning dispatch runner (MVP-001, issue #27).

A minimal deterministic control plane: task intake with observable features,
frozen task-start lane selection, worker dispatch through the delegate wrapper
(transport only), leader-executed verification, tree-bound acceptance receipts,
stagnation detection, and an append-only task record.

Designs out the frozen cascade's trust-boundary defects at birth:
  #17  receipts bind a tree signature; any mutation after verify stales them
  #18  a config-surface manifest (conftest.py, pytest.ini, ...) is hashed at
       init; new or changed verification-affecting files reject verification
  #19  verifier commands are argv arrays — no POSIX shlex on Windows paths
  #20  a workspace inside a parent git repo is refused unless it IS the root

Stdlib only, Python 3.10, Windows-native.

Commands:
  lane     --task task.json                          print the frozen lane decision
  init     --workspace ws --task task.json           validate + snapshot state
  dispatch --workspace ws --task task.json           run one worker attempt
  verify   --workspace ws --task task.json           leader-side acceptance check
  accept   --workspace ws --task task.json           refuse unless receipt fresh+green
  record   --workspace ws --task task.json [--wire wire.jsonl] [--pricing pricing.yaml]
  status   --workspace ws                            print runner state

Every command prints exactly one JSON object on stdout. Exit 0 = success,
1 = refused/failed, 2 = usage error, 3 = missing/bad config, 4 = internal.
"""

import argparse
import hashlib
import json
import os
import re
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
# flash lane: gpt-oss-120b — deepseek-v4-flash retired on Fireworks (404,
# probed 2026-08-25); gpt-oss-120b is the live cheap-worker substitute.
DEFAULT_AGENT_MAP = {
    "flash": "gpt-oss-worker",
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


def tree_signature(ws):
    """Signature of the exact tree a verifier ran against.

    Git workspace: HEAD + hash of `git status --porcelain` (tracked state).
    Non-git workspace: hash of the sorted (relpath, sha256) manifest.
    Either way, ANY mutation after verify produces a different signature (#17).
    """
    if _git_toplevel(ws) is not None:
        head = subprocess.run(["git", "-C", str(ws), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30)
        status = subprocess.run(
            ["git", "-C", str(ws), "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True, text=True, timeout=60)
        payload = (head.stdout + "\n" + status.stdout).encode("utf-8")
        return "git:" + _sha256_bytes(payload)
    rows = []
    for rel, p in _walk_files(ws):
        rows.append(f"{_sha256_file(p)}  {rel}")
    return "files:" + _sha256_bytes("\n".join(sorted(rows)).encode("utf-8"))


# ── state ───────────────────────────────────────────────────────────────

def _state_path(ws):
    return Path(ws) / STATE_DIR / "state.json"


def _receipt_path(ws, task_id):
    return Path(ws) / STATE_DIR / f"receipt-{task_id}.json"


def _load_state(ws):
    p = _state_path(ws)
    if not p.is_file():
        raise SystemExit(_emit({"error": "runner not initialized; run `init` first"}, 3))
    return _load_json(p, "runner state")


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
                 r"C:\Tools\kimi-router\delegate.py"):
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
    return _emit({"task_id": task["task_id"], "lane": lane, "reasons": reasons})


def cmd_init(args):
    ws = str(Path(args.workspace).resolve())
    task = load_task(args.task)
    check_workspace_root(ws)
    lane = task.get("lane") or lane_for(task["features"])[0]
    if lane not in LANES:
        raise SystemExit(_emit({"error": f"unknown lane: {lane}"}, 3))
    state = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task["task_id"],
        "workspace": ws,
        "lane": lane,
        "scope": task["scope"],
        "budget": task["budget"],
        "init_config_surface": config_surface(ws),
        "init_tree_sig": tree_signature(ws),
        "dispatches": [],
        "failures": [],
        "accepted": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _write_json_atomic(_state_path(ws), state)
    return _emit({"initialized": True, "task_id": task["task_id"], "lane": lane,
                  "config_surface_files": sorted(state["init_config_surface"])})


def cmd_dispatch(args):
    ws = str(Path(args.workspace).resolve())
    task = load_task(args.task)
    state = _load_state(ws)
    if state["accepted"]:
        raise SystemExit(_emit({"error": "task already accepted"}, 1))
    if len(state["dispatches"]) >= state["budget"]["max_dispatches"]:
        raise SystemExit(_emit({"error": "dispatch budget exhausted",
                                "dispatches": len(state["dispatches"])}, 1))
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
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    if envelope_status not in ("completed", "failed"):
        state["failures"].append({
            "kind": "provider_or_tool",
            "fingerprint": f"provider:{envelope_status}",
            "at": state["dispatches"][-1]["at"],
        })
    _write_json_atomic(_state_path(ws), state)
    cls, rec = classify_and_recommend(state, state["lane"])
    return _emit({
        "dispatched": True, "agent": agent, "lane": state["lane"],
        "envelope_status": envelope_status,
        "child_session_id": envelope.get("child_session_id"),
        "failure_class": cls, "recommendation": rec,
    })


def cmd_verify(args):
    ws = str(Path(args.workspace).resolve())
    task = load_task(args.task)
    state = _load_state(ws)

    # #18: the verification surface must be exactly what init saw.
    now_surface = config_surface(ws)
    if now_surface != state["init_config_surface"]:
        added = sorted(set(now_surface) - set(state["init_config_surface"]))
        changed = sorted(k for k in now_surface if k in state["init_config_surface"]
                         and now_surface[k] != state["init_config_surface"][k])
        removed = sorted(set(state["init_config_surface"]) - set(now_surface))
        receipt = {"task_id": task["task_id"], "passed": False,
                   "rejected": "config_surface_changed",
                   "added": added, "changed": changed, "removed": removed,
                   "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        _write_json_atomic(_receipt_path(ws, task["task_id"]), receipt)
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
        "tree_sig": tree_signature(ws),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _write_json_atomic(_receipt_path(ws, task["task_id"]), receipt)
    if not passed:
        state["failures"].append({
            "kind": "execution",
            "fingerprint": normalize_failure(output),
            "at": receipt["at"],
        })
        _write_json_atomic(_state_path(ws), state)
        cls, rec = classify_and_recommend(state, state["lane"])
        receipt["failure_class"] = cls
        receipt["recommendation"] = rec
    return _emit(receipt, 0 if passed else 1)


def cmd_accept(args):
    ws = str(Path(args.workspace).resolve())
    task = load_task(args.task)
    state = _load_state(ws)
    rp = _receipt_path(ws, task["task_id"])
    if not rp.is_file():
        raise SystemExit(_emit({"accepted": False, "reason": "no receipt; run verify"}, 1))
    receipt = _load_json(rp, "receipt")
    if not receipt.get("passed"):
        raise SystemExit(_emit({"accepted": False, "reason": "receipt not green",
                                "receipt": receipt}, 1))
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
    _write_json_atomic(_state_path(ws), state)
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


def cmd_record(args):
    ws = str(Path(args.workspace).resolve())
    task = load_task(args.task)
    state = _load_state(ws)
    row = {
        "task_id": task["task_id"],
        "lane": state["lane"],
        "dispatches": len(state["dispatches"]),
        "agents_used": sorted({d["agent"] for d in state["dispatches"]}),
        "accepted": state["accepted"],
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
    out = Path(ws) / STATE_DIR / "tasks.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return _emit({"recorded": True, "row": row, "log": str(out)})


def cmd_status(args):
    ws = str(Path(args.workspace).resolve())
    return _emit(_load_state(ws))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="runner", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("lane", "init", "dispatch", "verify", "accept", "record", "status"):
        p = sub.add_parser(name)
        if name != "status":
            p.add_argument("--task", required=True)
        if name != "lane":
            p.add_argument("--workspace", required=True)
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
