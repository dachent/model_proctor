#!/usr/bin/env python3
"""Generate a blinded scorecard from results.jsonl."""
import io
import json
import os
import statistics
import sys
from collections import defaultdict

# Force UTF-8 on stdout (Windows console defaults to cp1252)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_PATH = os.path.join(SCRIPT_DIR, "results.jsonl")
BLINDING_PATH = os.path.join(SCRIPT_DIR, "blinding-key.json")
SCORECARD_PATH = os.path.join(SCRIPT_DIR, "scorecard.md")


def load_results():
    if not os.path.exists(RESULTS_PATH):
        return []
    out = []
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def load_blinding():
    if not os.path.exists(BLINDING_PATH):
        return {}
    with open(BLINDING_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def median(values):
    return statistics.median(values) if values else None


def safe_div(a, b):
    return a / b if b else None


# ── aggregation ──────────────────────────────────────────────────────

def agg_config(results, config, sel=None):
    """Aggregate metrics for one config, optionally filtered by set."""
    rows = [r for r in results if r["config"] == config and (sel is None or r["set"] == sel)]
    if not rows:
        return {"n": 0}
    n = len(rows)
    acc = sum(1 for r in rows if r.get("acceptance_pass"))
    hid = sum(1 for r in rows if r.get("hidden_pass"))
    inv = sum(1 for r in rows if r.get("invariants_pass"))
    walls = [r["wall_clock_s"] for r in rows if r.get("wall_clock_s") is not None]
    toks = [r["est_tokens"] for r in rows if r.get("est_tokens") is not None]
    succ_rows = [r for r in rows if r.get("acceptance_pass")]
    succ_tokens = sum(r.get("est_tokens", 0) for r in succ_rows)
    first_rep = [r for r in rows if r.get("rep") == 1]
    first_pass = sum(1 for r in first_rep
                     if r.get("acceptance_pass") and r.get("agent_exit", 0) == 0
                     and not r.get("timed_out"))
    return {
        "n": n,
        "acc_pass": acc,
        "acc_rate": acc / n,
        "hidden_pass": hid,
        "hidden_rate": hid / n,
        "inv_pass": inv,
        "wall_med": median(walls),
        "tok_med": median(toks),
        "total_tokens": sum(toks),
        "succ_count": len(succ_rows),
        "cost_per_success": safe_div(sum(toks), len(succ_rows)) if succ_rows else None,
        "first_pass": first_pass,
        "first_total": len(first_rep),
        "first_rate": safe_div(first_pass, len(first_rep)) if first_rep else None,
    }


def agg_case(results, case_id, config):
    rows = [r for r in results if r["case_id"] == case_id and r["config"] == config]
    if not rows:
        return None
    walls = [r["wall_clock_s"] for r in rows]
    toks = [r["est_tokens"] for r in rows]
    acc = sum(1 for r in rows if r.get("acceptance_pass"))
    return {
        "n": len(rows),
        "acc_rate": acc / len(rows),
        "wall_med": median(walls),
        "tok_med": median(toks),
    }


# ── scorecard generation ─────────────────────────────────────────────

def fmt(v, unit=""):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}{unit}"
    return f"{v}{unit}"


def generate_scorecard(results, blinding):
    rev = {v: k for k, v in blinding.items()}
    configs = sorted(set(r["config"] for r in results))
    labels = {c: rev.get(c, c) for c in configs}
    base = "A"
    cand = "C"
    base_label = labels.get(base, base)
    cand_label = labels.get(cand, cand)

    case_ids = sorted(set(r["case_id"] for r in results))
    categories = sorted(set(r["category"] for r in results))

    lines = []
    w = lines.append

    w("# Kimi Code CLI Benchmark Scorecard")
    w("")
    w(f"Generated from {len(results)} run records in `results.jsonl`.")
    w("")
    w("## Blinding")
    w("")
    if blinding:
        for k, v in sorted(blinding.items()):
            w(f"- {k} → config **{v}**")
    else:
        w("- No blinding key found; using raw config labels.")
    w("")

    # ── per-config aggregate (tuning + holdout) ──
    for sel in ("tune", "holdout"):
        w(f"## Aggregate — {sel} set")
        w("")
        w("| Config | N | Success | Rate | Hidden | Hidden% | Wall(s) | Tokens | Cost/Succ | 1st-pass |")
        w("|--------|---|---------|------|--------|---------|---------|--------|-----------|----------|")
        for cfg in configs:
            a = agg_config(results, cfg, sel=sel)
            if a["n"] == 0:
                continue
            lbl = labels.get(cfg, cfg)
            w(f"| {lbl} | {a['n']} | {a['acc_pass']} | {fmt(a['acc_rate'])} | "
              f"{a['hidden_pass']} | {fmt(a.get('hidden_rate'))} | {fmt(a.get('wall_med'))} | "
              f"{fmt(a.get('tok_med'))} | {fmt(a.get('cost_per_success'))} | "
              f"{a.get('first_pass',0)}/{a.get('first_total',0)} |")
        w("")

    # ── per-category breakdown ──
    w("## Per-category breakdown (holdout)")
    w("")
    for cat in categories:
        cat_results = [r for r in results if r["category"] == cat and r["set"] == "holdout"]
        if not cat_results:
            continue
        w(f"### {cat}")
        w("")
        w("| Config | N | Success | Rate | Wall(s) | Tokens |")
        w("|--------|---|---------|------|---------|--------|")
        for cfg in configs:
            cat_cfg = [r for r in cat_results if r["config"] == cfg]
            if not cat_cfg:
                continue
            acc = sum(1 for r in cat_cfg if r.get("acceptance_pass"))
            walls = [r["wall_clock_s"] for r in cat_cfg]
            toks = [r["est_tokens"] for r in cat_cfg]
            lbl = labels.get(cfg, cfg)
            w(f"| {lbl} | {len(cat_cfg)} | {acc} | {fmt(acc/len(cat_cfg))} | "
              f"{fmt(median(walls))} | {fmt(median(toks))} |")
        w("")

    # ── per-case medians ──
    w("## Per-case medians (holdout)")
    w("")
    w("| Case | Config | N | Acc% | Wall(s) | Tokens |")
    w("|------|--------|---|------|---------|--------|")
    for cid in case_ids:
        for cfg in configs:
            a = agg_case(results, cid, cfg)
            if not a:
                continue
            # only show holdout cases
            case_rows = [r for r in results if r["case_id"] == cid and r["config"] == cfg]
            if not any(r["set"] == "holdout" for r in case_rows):
                continue
            lbl = labels.get(cfg, cfg)
            w(f"| {cid} | {lbl} | {a['n']} | {fmt(a['acc_rate'])} | "
              f"{fmt(a['wall_med'])} | {fmt(a['tok_med'])} |")
    w("")

    # ── adoption verdict (holdout only) ──
    w("## Adoption Verdict (holdout only)")
    w("")
    ba = agg_config(results, base, sel="holdout")
    ca = agg_config(results, cand, sel="holdout")
    if ba["n"] == 0 or ca["n"] == 0:
        w("Insufficient holdout data for verdict.")
    else:
        succ_ok = ca["acc_rate"] >= ba["acc_rate"]
        if ca.get("cost_per_success") is not None and ba.get("cost_per_success") is not None:
            cost_ok = ca["cost_per_success"] < ba["cost_per_success"]
        elif ca["succ_count"] == 0:
            cost_ok = False
        else:
            cost_ok = True  # undefined → success rate decides
        adopted = succ_ok and cost_ok
        w(f"- **{cand_label}** success rate: {fmt(ca['acc_rate'])} vs **{base_label}** {fmt(ba['acc_rate'])} "
          f"→ {'≥ ✓' if succ_ok else '< ✗'}")
        w(f"- **{cand_label}** cost/success: {fmt(ca.get('cost_per_success'))} vs **{base_label}** {fmt(ba.get('cost_per_success'))} "
          f"→ {'< ✓' if cost_ok else '≥ ✗'}")
        w(f"- **Verdict: {'ADOPTED' if adopted else 'NOT ADOPTED'}** {cand_label}")
    w("")

    # ── simple-case guardrail ──
    w("## Simple-case guardrail")
    w("")
    w(f"{cand_label} must not be slower than {base_label} on any simple_fix case median.")
    w("")
    sf_cases = sorted(set(r["case_id"] for r in results
                          if r["category"] == "simple_fix"))
    if sf_cases:
        w("| Case | Base wall(s) | Cand wall(s) | OK |")
        w("|------|-------------|-------------|----|")
        guardrail_ok = True
        for cid in sf_cases:
            bm = agg_case(results, cid, base)
            cm = agg_case(results, cid, cand)
            if not bm or not cm:
                continue
            ok = cm["wall_med"] <= bm["wall_med"]
            if not ok:
                guardrail_ok = False
            w(f"| {cid} | {fmt(bm['wall_med'])} | {fmt(cm['wall_med'])} | "
              f"{'✓' if ok else '✗'} |")
        w("")
        w(f"**Guardrail: {'PASS' if guardrail_ok else 'FAIL'}**")
    else:
        w("No simple_fix cases found.")
    w("")

    # ── first-pass success ──
    w("## First-pass success (rep 1, exit 0, no timeout)")
    w("")
    w("| Config | 1st-pass | Total | Rate |")
    w("|--------|----------|-------|------|")
    for cfg in configs:
        a = agg_config(results, cfg)
        if a["n"] == 0:
            continue
        lbl = labels.get(cfg, cfg)
        w(f"| {lbl} | {a.get('first_pass',0)} | {a.get('first_total',0)} | "
          f"{fmt(a.get('first_rate'))} |")
    w("")

    # ── regression proxy ──
    w("## Regression proxy (hidden_pass rate)")
    w("")
    w("| Config | Hidden pass | Total | Rate |")
    w("|--------|-------------|-------|------|")
    for cfg in configs:
        a = agg_config(results, cfg)
        if a["n"] == 0:
            continue
        lbl = labels.get(cfg, cfg)
        w(f"| {lbl} | {a.get('hidden_pass',0)} | {a['n']} | "
          f"{fmt(a.get('hidden_rate'))} |")
    w("")

    # ── raw table ──
    w("## Raw run table")
    w("")
    w("| Case | Config | Rep | Set | Wall(s) | Exit | Timeout | Acc | Hidden | Inv | Tokens |")
    w("|------|--------|-----|-----|---------|------|---------|-----|--------|-----|--------|")
    for r in sorted(results, key=lambda x: (x["case_id"], x["config"], x.get("rep", 0))):
        lbl = labels.get(r["config"], r["config"])
        w(f"| {r['case_id']} | {lbl} | {r.get('rep','')} | {r.get('set','')} | "
          f"{fmt(r.get('wall_clock_s'))} | {r.get('agent_exit','')} | "
          f"{'Y' if r.get('timed_out') else 'N'} | "
          f"{'✓' if r.get('acceptance_pass') else '✗'} | "
          f"{'✓' if r.get('hidden_pass') else '✗'} | "
          f"{'✓' if r.get('invariants_pass') else '✗'} | "
          f"{r.get('est_tokens','')} |")
    w("")

    return "\n".join(lines)


def main():
    results = load_results()
    if not results:
        print("No results found in results.jsonl")
        sys.exit(1)
    blinding = load_blinding()
    scorecard = generate_scorecard(results, blinding)
    with open(SCORECARD_PATH, "w", encoding="utf-8") as f:
        f.write(scorecard)
    print(f"Scorecard written to {SCORECARD_PATH}")
    print(f"\n{scorecard}")


if __name__ == "__main__":
    main()
