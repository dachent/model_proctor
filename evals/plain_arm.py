#!/usr/bin/env python3
"""plain_arm.py — the N arm of PREREG-plain-vs-proctored (#83 M4, early).

Dispatches the same fixed worker through the SAME transport (delegate.py)
with NO control plane: no lane, no init, no seal, no receipts, no budget,
no journal. The scorer grades the final tree against PRISTINE fixture checks
(equalizing the proctored arm's sealed-restore) and meters the wire with the
runner's own scan/price functions, A13 semantics included (unknown usage is
never zero).

Zero proctor machinery is the variable under test. Do not "improve" this
driver without amending the prereg first.
"""
import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRICING = REPO_ROOT / "evals" / "pricing.yaml"
DELEGATE = r"C:\Tools\model-proctor\delegate.py"


def _import(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pilot = _import(REPO_ROOT / "harnesses" / "kimi-code" / "runner" / "pilot.py",
                "pilot")
runner = _import(REPO_ROOT / "harnesses" / "kimi-code" / "runner" / "runner.py",
                "runner")

CASES = {c["id"]: c for c in json.loads(
    (REPO_ROOT / "evals" / "cases.yaml").read_text(encoding="utf-8"))}


def run_plain(case, out_root, agent, rep, timeout_s):
    ws = out_root / case["id"] / "plain" / f"rep{rep}"
    ws.mkdir(parents=True, exist_ok=True)
    pristine = out_root / case["id"] / "pristine"
    pristine.mkdir(parents=True, exist_ok=True)
    gen = pilot.FIXTURES / f"gen_{case['fixture']}.py"
    subprocess.run([sys.executable, str(gen), str(ws)], check=True,
                   capture_output=True, timeout=120)
    if not (pristine / "check.py").is_file():
        subprocess.run([sys.executable, str(gen), str(pristine)], check=True,
                       capture_output=True, timeout=120)

    t0 = time.time()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(case["task_prompt"])
        task_file = tf.name
    try:
        r = subprocess.run(
            [sys.executable, DELEGATE, "--agent", agent,
             "--workspace", str(ws), "--task-file", task_file,
             "--timeout", str(timeout_s)],
            capture_output=True, text=True, timeout=timeout_s + 300)
    finally:
        try:
            import os
            os.unlink(task_file)
        except OSError:
            pass
    envelope = {}
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError:
                continue
    sid = envelope.get("child_session_id")
    home = envelope.get("child_home")

    # Grading parity: the proctored arm's checks are restored from the seal
    # before they grade; copy the PRISTINE fixture checks over the workspace
    # so both arms are graded against identical exam bytes.
    for name in ("check.py", "hidden_check.py"):
        src = pristine / name
        if src.is_file():
            shutil.copy2(src, ws / name)
    check = subprocess.run([sys.executable, "check.py"], cwd=str(ws),
                           capture_output=True, timeout=120)
    hidden = subprocess.run([sys.executable, "hidden_check.py"], cwd=str(ws),
                            capture_output=True, timeout=120)

    # Meter with the runner's own math (A13: unknown is never zero).
    wires = pilot.find_wires([sid], t0, homes=[home] if home else [])
    records, totals = 0, {}
    problems = {"unparseable_lines": 0, "malformed_records": 0}
    for wire in wires:
        n, t, p = runner.scan_usage_records(wire)
        records += n
        problems["unparseable_lines"] += p["unparseable_lines"]
        problems["malformed_records"] += p["malformed_records"]
        for model, bucket in t.items():
            agg = totals.setdefault(model, {"inputOther": 0, "output": 0,
                                            "inputCacheRead": 0,
                                            "inputCacheCreation": 0})
            for k in agg:
                agg[k] += bucket[k]
    by_model, total = runner.price_tokens(totals, runner.load_pricing(PRICING))
    usage_unknown = bool(problems["unparseable_lines"]
                         or problems["malformed_records"] or records == 0)

    row = {
        "arm": "plain-direct", "task_id": case["id"], "rep": rep,
        "set": case["set"], "category": case["category"],
        "accepted": check.returncode == 0,
        "hidden_pass": hidden.returncode == 0,
        "envelope_status": envelope.get("status"),
        "usage_records": records, "usage_unknown": usage_unknown,
        "api_cost_usd": (None if usage_unknown or total is None else total),
        "cost_usd_by_model": by_model, "tokens_by_model": totals,
        "wire_files": len(wires),
        "wire_coverage": pilot.wire_coverage(wires),
        "child_session_ids": [sid] if sid else [],
        "wall_time_s": round(time.time() - t0, 1),
    }
    # The caller owns isolated homes; metering is done.
    if home:
        shutil.rmtree(home, ignore_errors=True)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True, help="comma-separated case ids")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--agent", default="glm-flash-worker")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--log", required=True, help="JSONL to append rows to")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    swept = pilot.sweep_orphan_homes()
    if swept:
        print(f"swept {swept} orphaned delegate-kimi-home dir(s)", flush=True)

    rows = []
    for cid in args.cases.split(","):
        case = CASES[cid]
        for rep in range(1, args.reps + 1):
            print(f"=== {cid} rep{rep} (plain) ===", flush=True)
            row = run_plain(case, out_root, args.agent, rep, args.timeout)
            rows.append(row)
            print(json.dumps(row, sort_keys=True), flush=True)

    with open(args.log, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    n = len(rows)
    ok = sum(1 for r in rows if r["accepted"])
    hid = sum(1 for r in rows if r["hidden_pass"])
    cost = sum(r["api_cost_usd"] or 0 for r in rows)
    unknown = sum(1 for r in rows if r["usage_unknown"])
    print(f"\nplain arm: {ok}/{n} accepted, {hid}/{n} hidden-pass, "
          f"metered cost ${cost:.4f} ({unknown} rows usage-unknown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
