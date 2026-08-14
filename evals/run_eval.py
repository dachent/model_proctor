#!/usr/bin/env python3
"""A/B/C benchmark runner for Kimi Code CLI configurations."""
import argparse
import json
import os
import random
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(SCRIPT_DIR, "fixtures")
CASES_PATH = os.path.join(SCRIPT_DIR, "cases.yaml")
RESULTS_PATH = os.path.join(SCRIPT_DIR, "results.jsonl")
BLINDING_PATH = os.path.join(SCRIPT_DIR, "blinding-key.json")
KIMI_EXE = r"C:\Users\BorisVaisman\.kimi-code\bin\kimi.exe"
RUNS_BASE = r"C:\Dev\bootstrap-state\kimi-router\evals\runs"

DEFAULT_REPS = 3
SIMPLE_FIX_REPS = 5


# ── loading & filtering ──────────────────────────────────────────────

def load_cases():
    with open(CASES_PATH, "r") as f:
        return json.load(f)


def filter_cases(cases, args):
    out = cases
    if args.cases:
        ids = set(args.cases.split(","))
        out = [c for c in out if c["id"] in ids]
    if args.set:
        out = [c for c in out if c["set"] == args.set]
    return out


def get_reps(case, reps_override):
    if reps_override is not None:
        return reps_override
    return SIMPLE_FIX_REPS if case["category"] == "simple_fix" else DEFAULT_REPS


# ── command execution helper ─────────────────────────────────────────

def run_cmd(cmd_str, cwd, timeout=60):
    """Run a shell command (shell=False), return (exit_code, stdout, stderr)."""
    parts = shlex.split(cmd_str)
    try:
        r = subprocess.run(parts, cwd=cwd, capture_output=True, timeout=timeout, text=True)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -1, "", str(e)


# ── fixture generation ───────────────────────────────────────────────

def generate_fixture(case, target_dir):
    gen = os.path.join(FIXTURES_DIR, f"gen_{case['fixture']}.py")
    if not os.path.exists(gen):
        raise FileNotFoundError(f"Generator not found: {gen}")
    os.makedirs(target_dir, exist_ok=True)
    r = subprocess.run([sys.executable, gen, target_dir], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Generator {case['fixture']} failed: {r.stderr}")


# ── kimi launch ──────────────────────────────────────────────────────

def launch_kimi(case, config, run_dir):
    skills_dir = os.path.join(SCRIPT_DIR, "skills", config)
    cmd = [KIMI_EXE, "--skills-dir", skills_dir, "-p", case["task_prompt"]]
    started = time.time()
    started_at = datetime.now().isoformat(timespec="seconds")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=run_dir, text=True,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=case["timeout_seconds"])
        agent_exit = proc.returncode
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except Exception:
            stdout, stderr = "", ""
        timed_out = True
        agent_exit = -1
    wall = time.time() - started
    stdout = stdout or ""
    stderr = stderr or ""
    # save raw output
    with open(os.path.join(run_dir, "agent_stdout.txt"), "w", encoding="utf-8") as f:
        f.write(stdout)
    with open(os.path.join(run_dir, "agent_stderr.txt"), "w", encoding="utf-8") as f:
        f.write(stderr)
    # cost accounting
    est_tokens = (len(case["task_prompt"].encode("utf-8")) + len(stdout.encode("utf-8"))) // 4
    tokens_reported = parse_reported_tokens(stdout)
    return {
        "started_at": started_at,
        "wall_clock_s": round(wall, 2),
        "agent_exit": agent_exit,
        "timed_out": timed_out,
        "est_tokens": est_tokens,
        "tokens_reported": tokens_reported,
    }


def parse_reported_tokens(text):
    """Try to extract reported token counts from agent output (kimi prints none by default)."""
    import re
    m = re.search(r"(?:tokens?|total[_ ]?tokens?)[:\s]+(\d+)", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


# ── acceptance / hidden / invariants ─────────────────────────────────

def check_acceptance(case, run_dir):
    code, out, err = run_cmd(case["acceptance"]["command"], run_dir)
    return {"acceptance_pass": code == 0}


def check_hidden(case, run_dir):
    code, out, err = run_cmd(case["hidden_test"]["command"], run_dir)
    return {"hidden_pass": code == 0}


def check_invariants(case, run_dir):
    invs = case.get("invariants", [])
    if not invs:
        return {"invariants_pass": True}
    for inv in invs:
        t = inv["type"]
        if t == "file_contains":
            p = os.path.join(run_dir, inv["path"])
            if not os.path.exists(p):
                return {"invariants_pass": False}
            with open(p, "r", encoding="utf-8") as f:
                if inv["contains"] not in f.read():
                    return {"invariants_pass": False}
        elif t == "file_exists":
            p = os.path.join(run_dir, inv["path"])
            if not os.path.exists(p):
                return {"invariants_pass": False}
        elif t == "command":
            code, _, _ = run_cmd(inv["command"], run_dir)
            expect = inv.get("expect_exit", 0)
            if code != expect:
                return {"invariants_pass": False}
    return {"invariants_pass": True}


# ── single run ───────────────────────────────────────────────────────

def run_single(case, config, rep):
    run_dir = os.path.join(RUNS_BASE, case["id"], config, str(rep))
    record = {
        "case_id": case["id"],
        "category": case["category"],
        "set": case["set"],
        "config": config,
        "rep": rep,
        "run_dir": run_dir,
        "error": None,
    }
    try:
        generate_fixture(case, run_dir)
    except Exception as e:
        record.update(
            started_at=datetime.now().isoformat(timespec="seconds"),
            wall_clock_s=0, agent_exit=-1, timed_out=False,
            acceptance_pass=False, hidden_pass=False, invariants_pass=False,
            est_tokens=0, tokens_reported=None, error=f"fixture gen: {e}",
        )
        append_result(record)
        print(f"  [{case['id']}/{config}/{rep}] fixture gen FAILED: {e}")
        return

    kimi_result = launch_kimi(case, config, run_dir)
    record.update(kimi_result)
    record.update(check_acceptance(case, run_dir))
    record.update(check_hidden(case, run_dir))
    record.update(check_invariants(case, run_dir))
    append_result(record)

    status = "PASS" if record["acceptance_pass"] else "FAIL"
    extra = " (TIMEOUT)" if record["timed_out"] else ""
    print(f"  [{case['id']}/{config}/{rep}] {status}{extra}  "
          f"wall={record['wall_clock_s']}s  est_tok={record['est_tokens']}")


def append_result(record):
    with open(RESULTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# ── resume support ───────────────────────────────────────────────────

def load_completed():
    completed = set()
    if not os.path.exists(RESULTS_PATH):
        return completed
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                completed.add((r["case_id"], r["config"], r["rep"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return completed


# ── blinding ─────────────────────────────────────────────────────────

def write_blinding_key(configs):
    shuffled = list(configs)
    random.shuffle(shuffled)
    labels = [f"Config{i+1}" for i in range(len(shuffled))]
    mapping = dict(zip(labels, shuffled))
    with open(BLINDING_PATH, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2)
    print(f"\nBlinding key written to {BLINDING_PATH}: {mapping}")


# ── self-test ────────────────────────────────────────────────────────

def run_self_test(cases):
    """Generate all fixtures, run all check scripts, verify expected initial states."""
    scratch = tempfile.mkdtemp(prefix="kimi_eval_selftest_")
    print(f"Self-test scratch dir: {scratch}")
    print(f"Testing {len(cases)} cases\n")
    passed = 0
    failed = 0
    for case in cases:
        cid = case["id"]
        fixture_dir = os.path.join(scratch, cid)
        # 1. run generator
        try:
            generate_fixture(case, fixture_dir)
        except Exception as e:
            print(f"  {cid:30s}  GEN FAIL  {e}")
            failed += 1
            continue
        # 2. run acceptance
        acc_code, acc_out, acc_err = run_cmd(case["acceptance"]["command"], fixture_dir)
        acc_passed = (acc_code == 0)
        expect_fail = case.get("initially_failing", False)
        acc_ok = (acc_passed != expect_fail)  # XOR: if expect_fail, pass means NOT ok
        # 3. run hidden_test
        hid_code, hid_out, hid_err = run_cmd(case["hidden_test"]["command"], fixture_dir)
        hid_passed = (hid_code == 0)
        # For initially_failing, hidden should also fail; for others, just check it runs
        if expect_fail:
            hid_ok = (not hid_passed)
        else:
            hid_ok = True  # just verify it executed
        # 4. run invariants (against unmodified fixture)
        inv_ok = True
        for inv in case.get("invariants", []):
            if inv["type"] == "file_exists":
                p = os.path.join(fixture_dir, inv["path"])
                if not os.path.exists(p):
                    inv_ok = False
                    break
            elif inv["type"] == "file_contains":
                p = os.path.join(fixture_dir, inv["path"])
                if os.path.exists(p):
                    with open(p) as f:
                        if inv["contains"] not in f.read():
                            inv_ok = False
                            break
                else:
                    inv_ok = False
                    break

        gen_ok = True
        all_ok = acc_ok and hid_ok and inv_ok
        if all_ok:
            passed += 1
            tag = "OK"
        else:
            failed += 1
            tag = "FAIL"
        exp = "expect-fail" if expect_fail else "expect-pass"
        acc_s = f"acc={acc_code}({'pass' if acc_passed else 'fail'},{exp})"
        hid_s = f"hid={hid_code}({'pass' if hid_passed else 'fail'})"
        inv_s = f"inv={'ok' if inv_ok else 'bad'}"
        print(f"  {cid:30s}  {tag:4s}  {acc_s:30s}  {hid_s:20s}  {inv_s}")
        if not all_ok:
            if acc_out.strip():
                print(f"       acc stdout: {acc_out.strip()[:120]}")
            if acc_err.strip():
                print(f"       acc stderr: {acc_err.strip()[:120]}")

    print(f"\nSelf-test: {passed}/{len(cases)} passed, {failed} failed")
    return failed == 0


# ── main ─────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Kimi Code CLI benchmark runner")
    p.add_argument("--cases", default=None, help="Comma-separated case IDs (default: all)")
    p.add_argument("--configs", default="A,B,C", help="Comma-separated config letters (default: A,B,C)")
    p.add_argument("--reps", type=int, default=None, help=f"Reps per case (default: {DEFAULT_REPS}, {SIMPLE_FIX_REPS} for simple_fix)")
    p.add_argument("--set", default=None, choices=["tune", "holdout"], help="Filter by set")
    p.add_argument("--self-test", action="store_true", help="Run fixture self-test (no kimi)")
    p.add_argument("--resume", action="store_true", help="Skip completed (case,config,rep) records")
    return p.parse_args()


def main():
    args = parse_args()
    cases = load_cases()
    cases = filter_cases(cases, args)

    if args.self_test:
        ok = run_self_test(cases)
        sys.exit(0 if ok else 1)

    if not cases:
        print("No cases selected.")
        sys.exit(1)

    configs = [c.strip() for c in args.configs.split(",")]
    # verify skills dirs
    for cfg in configs:
        sd = os.path.join(SCRIPT_DIR, "skills", cfg)
        if not os.path.isdir(sd):
            print(f"ERROR: skills dir not found for config '{cfg}': {sd}")
            sys.exit(1)

    completed = load_completed() if args.resume else set()
    if completed:
        print(f"Resume: skipping {len(completed)} completed runs")

    total = 0
    for case in cases:
        reps = get_reps(case, args.reps)
        total += reps * len(configs)
    print(f"Running {len(cases)} cases x {len(configs)} configs = {total} runs\n")

    for case in cases:
        reps = get_reps(case, args.reps)
        print(f"[{case['id']}] ({case['category']}, {case['set']}, {reps} reps)")
        for rep in range(1, reps + 1):
            for cfg in configs:
                key = (case["id"], cfg, rep)
                if key in completed:
                    print(f"  [{case['id']}/{cfg}/{rep}] skipped (completed)")
                    continue
                run_single(case, cfg, rep)

    write_blinding_key(configs)
    print(f"\nResults: {RESULTS_PATH}")
    print("Run: python report.py to generate scorecard")


if __name__ == "__main__":
    main()
