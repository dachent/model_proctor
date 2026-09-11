"""E-ship driver: model-mode vs plain, interleaved at the rep level.

PREREG-model-mode E-ship (sealed 1be1f3c). Arm S-model dispatches via the
Phase-1 path (delegate.py --model fireworks/glm-5p3-flash); arm S-plain via
the roster path (delegate.py --agent glm-flash-worker). 6 reps per case per
arm = 120 runs, alternating arms per rep. Prompt via external --task-file
in BOTH arms. Graded identically (pristine checks). Wire-metered, corrected
pricing, A13 unknown-never-zero.
"""
import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRICING = REPO_ROOT / "evals" / "pricing.yaml"
DELEGATE = r"C:\Tools\model-proctor\delegate.py"
MODEL = "fireworks/glm-5p3-flash"
AGENT = "glm-flash-worker"


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


def run_one(case, out_root, arm, rep, timeout_s):
    """One dispatch. arm: 'S-model' (delegate --model) or 'S-plain'
    (delegate --agent). Fresh fixture; prompt via external task-file."""
    ws = out_root / case["id"] / arm / f"rep{rep}"
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
    import os
    fd, task_file = None, None
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(case["task_prompt"])
        task_file = tf.name
    try:
        argv = [sys.executable, DELEGATE, "--workspace", str(ws),
                "--task-file", task_file, "--timeout", str(timeout_s)]
        if arm == "S-model":
            argv += ["--model", MODEL]
        else:
            argv += ["--agent", AGENT]
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout_s + 300)
    finally:
        try:
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

    # Grading parity: pristine fixture checks over the workspace.
    for name in ("check.py", "hidden_check.py"):
        src = pristine / name
        if src.is_file():
            shutil.copy2(src, ws / name)
    check = subprocess.run([sys.executable, "check.py"], cwd=str(ws),
                           capture_output=True, timeout=120)
    hidden = subprocess.run([sys.executable, "hidden_check.py"], cwd=str(ws),
                            capture_output=True, timeout=120)

    wires = pilot.find_wires([sid], t0, homes=[home] if home else [])
    records, totals = 0, {}
    for wire in wires:
        n, t = runner.sum_usage_records(wire)
        records += n
        for model, bucket in t.items():
            agg = totals.setdefault(model, {"inputOther": 0, "output": 0,
                                            "inputCacheRead": 0,
                                            "inputCacheCreation": 0})
            for k in agg:
                agg[k] += bucket[k]
    by_model, total = runner.price_tokens(totals, runner.load_pricing(PRICING))
    usage_unknown = records == 0

    row = {
        "arm": arm, "task_id": case["id"], "rep": rep,
        "set": case["set"], "category": case["category"],
        "accepted": check.returncode == 0,
        "hidden_pass": hidden.returncode == 0,
        "envelope_status": envelope.get("status"),
        "usage_records": records, "usage_unknown": usage_unknown,
        "api_cost_usd": (None if usage_unknown or total is None else total),
        "tokens_total": sum(sum(b.values()) for b in totals.values()),
        "wire_files": len(wires),
        "child_session_ids": [sid] if sid else [],
        "wall_time_s": round(time.time() - t0, 1),
    }
    if home:
        shutil.rmtree(home, ignore_errors=True)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--reps", type=int, default=6)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--log", required=True)
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
            # INTERLEAVED at the rep level: alternate arms per rep, cancelling
            # time-of-day and session variance (the F4 lesson).
            for arm in ("S-model", "S-plain") if rep % 2 == 1 else ("S-plain", "S-model"):
                print(f"=== {cid} rep{rep} ({arm}) ===", flush=True)
                row = run_one(case, out_root, arm, rep, args.timeout)
                rows.append(row)
                print(json.dumps(row, sort_keys=True), flush=True)

    with open(args.log, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    n = len(rows)
    for arm in ("S-model", "S-plain"):
        rs = [r for r in rows if r["arm"] == arm]
        ok = sum(1 for r in rs if r["accepted"])
        hid = sum(1 for r in rs if r["hidden_pass"])
        cost = sum(r["api_cost_usd"] or 0 for r in rs)
        unknown = sum(1 for r in rs if r["usage_unknown"])
        print(f"\n{arm}: {ok}/{len(rs)} accepted, {hid}/{len(rs)} hidden, "
              f"cost ${cost:.4f} ({unknown} usage-unknown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
