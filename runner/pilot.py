#!/usr/bin/env python3
"""pilot — run N eval cases through runner.py with REAL delegate dispatches.

This is the MVP-001 pilot gate (issue #27 -> #26 Phase 2 go/no-go input):
10 cases across 7 categories, each executed as init -> dispatch -> verify ->
accept -> record, with the runner's own recommendation loop (retry_same /
lateral_switch) followed deterministically. Workspaces live outside
cloud-synced folders; the summary lands in evals/ as durable evidence.

Usage:
    python runner/pilot.py [--cases id,id,...] [--dry-run]
                           [--out-dir C:\\Dev\\bootstrap-state\\model-proctor\\pilot]

Stdlib only. Real dispatches spawn real CLIs and cost real tokens.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "runner" / "runner.py"
FIXTURES = ROOT / "evals" / "fixtures"
CASES = ROOT / "evals" / "cases.yaml"  # JSON syntax
PRICING = ROOT / "evals" / "pricing.yaml"
PILOT_LOG = ROOT / "evals" / "pilot-2026-08-25.jsonl"

DEFAULT_OUT = r"C:\Dev\bootstrap-state\model-proctor\pilot"
def _sessions_root():
    """Return the sessions directory, respecting KIMI_CODE_HOME if set."""
    home = os.environ.get("KIMI_CODE_HOME")
    if home:
        return Path(home) / "sessions"
    return Path(os.environ["USERPROFILE"]) / ".kimi-code" / "sessions"

# 10 cases, 7 categories — tune-weighted for continuity with the 2026-07-30
# pilot, plus holdout cases never run through any router.
DEFAULT_CASES = [
    "sf1_off_by_one", "sf3_missing_return",      # simple_fix
    "ex1_call_chain", "ex3_data_flow",           # exploration
    "me3_add_type_hints",                        # mechanical
    "mf1_cache_layer", "mf2_input_validation",   # multifile
    "db1_shared_state",                          # debugging
    "mg1_config_migration",                      # migration
    "sec1_path_traversal",                       # security
]

# Honest feature assignment per category (frozen before the run).
CATEGORY_FEATURES = {
    "simple_fix":  {"bounded": True, "known_location": True, "objective_acceptance": True},
    "debugging":   {"bounded": True, "known_location": True, "objective_acceptance": True},
    "security":    {"bounded": True, "known_location": True, "objective_acceptance": True},
    "quality":     {"bounded": True, "known_location": True, "objective_acceptance": True},
    "exploration": {"unfamiliar_repo": True, "objective_acceptance": True},
    "mechanical":  {"multi_module": True, "objective_acceptance": True},
    "multifile":   {"multi_module": True, "objective_acceptance": True},
    "migration":   {"multi_module": True, "objective_acceptance": True},
}


def run_runner(*argv, timeout=900):
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, timeout=timeout)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def find_wires(session_ids, not_before, homes=()):
    """Locate wire.jsonl files for the given child session ids.

    With TOOL-013 isolation (delegate.py injects a seeded per-dispatch
    KIMI_CODE_HOME), wires live under <child_home>/sessions/; the env/default
    home is only a fallback for isolation-disabled runs.
    """
    roots = [Path(h) / "sessions" for h in homes if h] + [_sessions_root()]
    wires = []
    for sid in session_ids:
        if not sid:
            continue
        for root in roots:
            if not root.is_dir():
                continue
            for p in root.glob(f"*/{sid}/agents/*/wire.jsonl"):
                try:
                    if p.stat().st_mtime >= not_before - 5:
                        wires.append(str(p))
                except OSError:
                    continue
    return sorted(set(wires))


def sweep_orphan_homes(max_age_s=3600):
    """Delete orphaned delegate-kimi-home-* temp dirs (crash leftovers)."""
    removed = 0
    for p in Path(tempfile.gettempdir()).glob("delegate-kimi-home-*"):
        try:
            if time.time() - p.stat().st_mtime > max_age_s:
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def run_case(case, out_root, dry_run, lane_override=None, max_dispatches=None,
             rep=None, keep_homes=False):
    ws = out_root / case["id"]
    if lane_override:
        ws = ws / lane_override
    if rep is not None:
        ws = ws / f"rep{rep}"
    ws.mkdir(parents=True, exist_ok=True)
    gen = FIXTURES / f"gen_{case['fixture']}.py"
    subprocess.run([sys.executable, str(gen), str(ws)], check=True,
                   capture_output=True, timeout=120)

    task = {
        "task_id": case["id"],
        "prompt": case["task_prompt"],
        "features": dict(CATEGORY_FEATURES[case["category"]]),
        "scope": ["."],
        "verifier": {"argv": ["{python}", "check.py"]},
        # TOOL-014: seal the hidden check too — it lives in the workspace during
        # the run, so a worker could read/rewrite it; verify restores the sealed
        # copy and flags tampering, and pilot's post-accept hidden run then
        # executes the restored (== sealed) file.
        "seal": ["check.py", "hidden_check.py"],
        "budget": {"max_dispatches": max_dispatches or 3, "max_stagnant": 3,
                   "timeout_s": min(case["timeout_seconds"] * 2, 600)},
    }
    if lane_override:
        task["lane"] = lane_override
    task_path = ws / "task.json"
    task_path.write_text(json.dumps(task, indent=2), encoding="utf-8")

    if dry_run:
        rc, lane = run_runner("lane", "--task", str(task_path))
        return {"task_id": case["id"], "category": case["category"],
                "set": case["set"], "lane": lane.get("lane"), "dry_run": True}

    t0 = time.time()
    summary = {"task_id": case["id"], "category": case["category"],
               "set": case["set"], "attempts": []}
    if rep is not None:
        summary["rep"] = rep

    rc, out = run_runner("init", "--workspace", str(ws), "--task", str(task_path))
    if rc != 0:
        summary["error"] = out.get("error")
        return summary
    summary["lane"] = out["lane"]

    session_ids = []
    child_homes = []
    accepted = False
    recommendation = None
    for attempt in range(task["budget"]["max_dispatches"]):
        if recommendation and recommendation.get("action") == "lateral_switch":
            task["lane"] = recommendation["to_lane"]
            task_path.write_text(json.dumps(task, indent=2), encoding="utf-8")
            run_runner("init", "--workspace", str(ws), "--task", str(task_path))
            summary["switched_to"] = recommendation["to_lane"]
        started = time.time()
        rc, disp = run_runner("dispatch", "--workspace", str(ws),
                              "--task", str(task_path),
                              timeout=task["budget"]["timeout_s"] + 300)
        summary["attempts"].append({
            "agent": disp.get("agent"), "lane": disp.get("lane"),
            "envelope_status": disp.get("envelope_status"),
            "failure_class": disp.get("failure_class"),
        })
        if disp.get("child_session_id"):
            session_ids.append(disp["child_session_id"])
        if disp.get("child_home"):
            child_homes.append(disp["child_home"])
        if rc != 0:
            break
        if disp.get("envelope_status") not in ("completed", "failed"):
            break  # provider/tool failure: no alternate lane in the pilot

        rc, ver = run_runner("verify", "--workspace", str(ws),
                             "--task", str(task_path))
        summary["attempts"][-1]["verify_passed"] = ver.get("passed")
        recommendation = ver.get("recommendation")
        if ver.get("passed"):
            rc, acc = run_runner("accept", "--workspace", str(ws),
                                 "--task", str(task_path))
            accepted = bool(acc.get("accepted"))
            summary["accept"] = acc
            break
    summary["accepted"] = accepted

    # Independent quality signal: the hidden check, run after the loop.
    hidden = subprocess.run([sys.executable, "hidden_check.py"], cwd=ws,
                            capture_output=True, timeout=120)
    summary["hidden_pass"] = hidden.returncode == 0

    wires = find_wires(session_ids, t0, homes=child_homes)
    summary["wire_files"] = len(wires)
    argv = ["record", "--workspace", str(ws), "--task", str(task_path)]
    if wires:
        argv += ["--wire", *wires, "--pricing", str(PRICING)]
    rc, rec = run_runner(*argv)
    row = rec.get("row", {})
    summary["record"] = {k: row.get(k) for k in (
        "dispatches", "accepted", "wall_time_seconds", "usage_records",
        "api_cost_usd", "tokens_by_model")}
    summary["wall_time_s"] = round(time.time() - t0, 1)
    # TOOL-013: the caller owns isolated homes; metering is done, delete them.
    if child_homes:
        summary["child_homes_cleaned"] = 0
        for h in child_homes:
            if keep_homes:
                summary.setdefault("child_homes_kept", []).append(h)
            else:
                shutil.rmtree(h, ignore_errors=True)
                summary["child_homes_cleaned"] += 1
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=None, help="comma-separated case ids")
    parser.add_argument("--out-dir", default=DEFAULT_OUT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--lane", choices=("flash", "glm", "k3"), default=None,
                        help="force this lane for every case (fixed-arm mode)")
    parser.add_argument("--reps", type=int, default=1,
                        help="repetitions per case (fresh fixture workspace each)")
    parser.add_argument("--max-dispatches", type=int, default=None,
                        help="per-case dispatch cap (1 = fixed arm, no rescue)")
    parser.add_argument("--log", default=str(PILOT_LOG),
                        help="summary JSONL to append to")
    parser.add_argument("--keep-kimi-home", action="store_true",
                        help="do not delete the temporary KIMI_CODE_HOME after the run")
    args = parser.parse_args()

    cases = {c["id"]: c for c in json.loads(CASES.read_text(encoding="utf-8"))}
    ids = args.cases.split(",") if args.cases else DEFAULT_CASES
    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    # TOOL-013: isolation lives at the delegate chokepoint (per-dispatch seeded
    # KIMI_CODE_HOME). A process-level os.environ set here cannot cross the
    # delegate's environment allowlist — verified inert on 2026-08-25 — so the
    # pilot only sweeps orphaned homes and cleans up per-dispatch ones after
    # metering (see run_case).
    swept = sweep_orphan_homes()
    if swept:
        print(f"swept {swept} orphaned delegate-kimi-home dir(s)", flush=True)

    summaries = []
    for cid in ids:
        for rep in range(1, args.reps + 1):
            label = f"{cid} rep{rep}" if args.reps > 1 else cid
            print(f"=== {label} ===", flush=True)
            s = run_case(cases[cid], out_root, args.dry_run,
                         lane_override=args.lane,
                         max_dispatches=args.max_dispatches,
                         rep=rep if args.reps > 1 else None,
                         keep_homes=args.keep_kimi_home)
            if args.lane:
                s["arm"] = args.lane
            summaries.append(s)
            print(json.dumps(s, sort_keys=True), flush=True)

    if not args.dry_run:
        with open(args.log, "a", encoding="utf-8") as f:
            for s in summaries:
                f.write(json.dumps(s, sort_keys=True) + "\n")
        n = len(summaries)
        ok = sum(1 for s in summaries if s.get("accepted"))
        hidden = sum(1 for s in summaries if s.get("hidden_pass"))
        cost = sum((s.get("record") or {}).get("api_cost_usd") or 0
                   for s in summaries)
        print(f"\npilot: {ok}/{n} accepted, {hidden}/{n} hidden-pass, "
              f"total metered cost ${cost:.4f}")
        print(f"summary appended to {args.log}")


if __name__ == "__main__":
    main()
