#!/usr/bin/env python3
"""Generate a blinded scorecard from results.jsonl.

Cost is metered USD. `results-metered.jsonl` is joined onto the run rows by
(case_id, config, rep) and its `total_cost_usd` is the only cost input:
`est_tokens` is an I/O-volume proxy, not money, and never stands in for a
dollar figure. When the metered set is absent or a row is unmatched the
scorecard declares `cost_source: "unmetered"` and the cost cell reads "—".
"""
import io
import json
import os
import statistics
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_PATH = os.path.join(SCRIPT_DIR, "results.jsonl")
METERED_PATH = os.path.join(SCRIPT_DIR, "results-metered.jsonl")
BLINDING_PATH = os.path.join(SCRIPT_DIR, "blinding-key.json")
SCORECARD_PATH = os.path.join(SCRIPT_DIR, "scorecard.md")


def load_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def load_results(path=None):
    return load_jsonl(path or RESULTS_PATH)


def load_blinding(path=None):
    p = path or BLINDING_PATH
    if not os.path.exists(p):
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def load_metered_index(path=None):
    """(case_id, config, rep) -> metered USD, plus whether the file exists."""
    p = path or METERED_PATH
    if not os.path.exists(p):
        return {}, False
    index = {}
    for row in load_jsonl(p):
        cost = row.get("total_cost_usd")
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            cost = None
        index[(row.get("case_id"), row.get("config"), row.get("rep"))] = cost
    return index, True


def join_costs(results, index, present):
    """Attach metered USD to every run row; a token count is never a fallback."""
    unmatched = []
    for r in results:
        cost = index.get((r.get("case_id"), r.get("config"), r.get("rep")))
        r["total_cost_usd"] = cost
        if cost is None:
            unmatched.append((r.get("case_id"), r.get("config"), r.get("rep")))
    matched = len(results) - len(unmatched)
    return {
        "present": present,
        "rows": len(results),
        "matched": matched,
        "unmatched": unmatched,
        "source": "metered" if results and matched == len(results) else "unmetered",
    }


def median(values):
    return statistics.median(values) if values else None


def safe_div(a, b):
    return a / b if b else None


# ── aggregation ──────────────────────────────────────────────────────

def agg_config(results, config, sel=None):
    """Aggregate metrics for one config, optionally filtered by set."""
    rows = [r for r in results if r["config"] == config and (sel is None or r.get("set") == sel)]
    if not rows:
        return {"n": 0}
    n = len(rows)
    acc = sum(1 for r in rows if r.get("acceptance_pass"))
    hid = sum(1 for r in rows if r.get("hidden_pass"))
    inv = sum(1 for r in rows if r.get("invariants_pass"))
    walls = [r["wall_clock_s"] for r in rows if r.get("wall_clock_s") is not None]
    toks = [r["est_tokens"] for r in rows if r.get("est_tokens") is not None]
    succ_rows = [r for r in rows if r.get("acceptance_pass")]
    costed = [r for r in rows if r.get("total_cost_usd") is not None]
    # Cost per success is metered USD per successful run. If any row in the
    # group is unmetered the figure is unknown — it is reported as None (which
    # renders as "—") rather than as a token count.
    fully_metered = len(costed) == n
    cost_per_success = None
    if fully_metered and succ_rows:
        cost_per_success = safe_div(sum(r["total_cost_usd"] for r in succ_rows), len(succ_rows))
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
        "cost_per_success": cost_per_success,
        "cost_source": "metered" if fully_metered else "unmetered",
        "cost_metered_rows": len(costed),
        "first_pass": first_pass,
        "first_total": len(first_rep),
        "first_rate": safe_div(first_pass, len(first_rep)) if first_rep else None,
    }


def agg_case(results, case_id, config):
    rows = [r for r in results if r["case_id"] == case_id and r["config"] == config]
    if not rows:
        return None
    walls = [r["wall_clock_s"] for r in rows if r.get("wall_clock_s") is not None]
    toks = [r["est_tokens"] for r in rows if r.get("est_tokens") is not None]
    acc = sum(1 for r in rows if r.get("acceptance_pass"))
    return {
        "n": len(rows),
        "acc_rate": acc / len(rows),
        "wall_med": median(walls),
        "tok_med": median(toks),
    }


def compute_guardrail(results, base, cand):
    """Simple_fix wall-clock guardrail: cand must not be slower on any case."""
    sf_cases = sorted(set(r["case_id"] for r in results
                          if r.get("category") == "simple_fix"))
    comparisons = []
    for cid in sf_cases:
        bm = agg_case(results, cid, base)
        cm = agg_case(results, cid, cand)
        if not bm or not cm:
            continue
        if bm["wall_med"] is None or cm["wall_med"] is None:
            continue
        comparisons.append({
            "case_id": cid,
            "base_wall": bm["wall_med"],
            "cand_wall": cm["wall_med"],
            "ok": cm["wall_med"] <= bm["wall_med"],
        })
    return {
        "sf_cases": sf_cases,
        "comparisons": comparisons,
        "ok": all(c["ok"] for c in comparisons),
        "measured": bool(comparisons),
    }


def compute_verdict(results, base, cand, guardrail):
    """Adoption decision. Every gate is computed before the verdict is emitted."""
    ba = agg_config(results, base, sel="holdout")
    ca = agg_config(results, cand, sel="holdout")
    if ba["n"] == 0 or ca["n"] == 0:
        return None
    succ_ok = ca["acc_rate"] >= ba["acc_rate"]
    # Undefined cost cannot pass the cost gate: the defect this replaces treated
    # "no dollar figure" as a pass and let the token proxy decide.
    cost_ok = (ca.get("cost_per_success") is not None
               and ba.get("cost_per_success") is not None
               and ca["cost_per_success"] < ba["cost_per_success"])
    guardrail_ok = bool(guardrail["ok"])
    return {
        "base": ba,
        "cand": ca,
        "succ_ok": succ_ok,
        "cost_ok": cost_ok,
        "guardrail_ok": guardrail_ok,
        "adopted": succ_ok and cost_ok and guardrail_ok,
    }


# ── scorecard generation ─────────────────────────────────────────────

def fmt(v, unit=""):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}{unit}"
    return f"{v}{unit}"


def fmt_money(v):
    return "—" if v is None else f"${v:.4f}"


def generate_scorecard(results, blinding, cost_info=None):
    # blinding-key.json maps Config-name -> blind letter and the run rows carry
    # the letter in `config`, so letter -> name is the direction needed here.
    rev = {v: k for k, v in blinding.items()}

    def label_for(cfg):
        name = rev.get(cfg)
        return name if name else f"{cfg} (unkeyed)"

    configs = sorted(set(r["config"] for r in results))
    base = "A"
    cand = "C"
    base_label = label_for(base)
    cand_label = label_for(cand)

    case_ids = sorted(set(r["case_id"] for r in results))
    categories = sorted(set(r["category"] for r in results))

    # Compute both decision inputs before emitting either of them.
    guardrail = compute_guardrail(results, base, cand)
    verdict = compute_verdict(results, base, cand, guardrail)

    lines = []
    w = lines.append

    w("# Kimi Code CLI Benchmark Scorecard")
    w("")
    w(f"Generated from {len(results)} run records in `results.jsonl`.")
    w("")
    w("## Blinding")
    w("")
    if blinding:
        w("Key direction: Config-name → blind letter. Tables below unblind the")
        w("letter a run row carries; a letter with no key entry is marked `(unkeyed)`.")
        w("")
        for k, v in sorted(blinding.items()):
            w(f"- {k} → config **{v}**")
    else:
        w("- No blinding key found; using raw config labels.")
    w("")

    # ── cost source ──
    info = cost_info or {"present": False, "rows": len(results), "matched": 0,
                         "unmatched": [], "source": "unmetered"}
    w("## Cost source")
    w("")
    if info["source"] == "metered":
        w(f'- `cost_source: "metered"` — {info["matched"]}/{info["rows"]} run rows joined '
          f'from `results-metered.jsonl` by `(case_id, config, rep)`; every cost figure is '
          f'metered USD.')
    else:
        w(f'- `cost_source: "unmetered"` — {info["matched"]}/{info["rows"]} run rows carry a '
          f'metered `total_cost_usd` from `results-metered.jsonl`.')
        if not info["present"]:
            w("- `results-metered.jsonl` is absent or empty.")
        if info["unmatched"]:
            shown = ", ".join(f"{c}/{g}/r{r}" for c, g, r in info["unmatched"][:5])
            more = "" if len(info["unmatched"]) <= 5 else \
                f" (+{len(info['unmatched']) - 5} more)"
            w(f"- Unmatched rows (case/config/rep): {shown}{more}")
        w("- Unmetered cost renders as `—`. A token count is never substituted for dollars,")
        w("  and the adoption verdict cannot pass the cost gate on an unknown cost.")
    w("")

    # ── per-config aggregate (tuning + holdout) ──
    for sel in ("tune", "holdout"):
        w(f"## Aggregate — {sel} set")
        w("")
        w("| Config | N | Success | Rate | Hidden | Hidden% | Wall(s) | Tokens | "
          "Cost/Succ ($) | Cost src | 1st-pass |")
        w("|--------|---|---------|------|--------|---------|---------|--------|"
          "---------------|----------|----------|")
        for cfg in configs:
            a = agg_config(results, cfg, sel=sel)
            if a["n"] == 0:
                continue
            w(f"| {label_for(cfg)} | {a['n']} | {a['acc_pass']} | {fmt(a['acc_rate'])} | "
              f"{a['hidden_pass']} | {fmt(a.get('hidden_rate'))} | {fmt(a.get('wall_med'))} | "
              f"{fmt(a.get('tok_med'))} | {fmt_money(a.get('cost_per_success'))} | "
              f"{a.get('cost_source', 'unmetered')} | "
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
            walls = [r["wall_clock_s"] for r in cat_cfg if r.get("wall_clock_s") is not None]
            toks = [r["est_tokens"] for r in cat_cfg if r.get("est_tokens") is not None]
            w(f"| {label_for(cfg)} | {len(cat_cfg)} | {acc} | {fmt(acc/len(cat_cfg))} | "
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
            w(f"| {cid} | {label_for(cfg)} | {a['n']} | {fmt(a['acc_rate'])} | "
              f"{fmt(a['wall_med'])} | {fmt(a['tok_med'])} |")
    w("")

    # ── simple-case guardrail (emitted before the verdict that depends on it) ──
    w("## Simple-case guardrail")
    w("")
    w(f"{cand_label} must not be slower than {base_label} on any simple_fix case median.")
    w("")
    if guardrail["comparisons"]:
        w("| Case | Base wall(s) | Cand wall(s) | OK |")
        w("|------|-------------|-------------|----|")
        for c in guardrail["comparisons"]:
            w(f"| {c['case_id']} | {fmt(c['base_wall'])} | {fmt(c['cand_wall'])} | "
              f"{'✓' if c['ok'] else '✗'} |")
        w("")
        w(f"**Guardrail: {'PASS' if guardrail['ok'] else 'FAIL'}**")
    elif guardrail["sf_cases"]:
        w("**Guardrail: PASS (vacuous — no simple_fix case has data for both arms)**")
    else:
        w("**Guardrail: PASS (vacuous — no simple_fix cases found)**")
    w("")

    # ── adoption verdict (holdout only) ──
    w("## Adoption Verdict (holdout only)")
    w("")
    w("Adopted requires all three gates: success rate, metered cost per success, guardrail.")
    w("")
    if verdict is None:
        w("Insufficient holdout data for verdict.")
    else:
        ba, ca = verdict["base"], verdict["cand"]
        if verdict["cost_ok"]:
            cost_mark = "< ✓"
        elif ca.get("cost_per_success") is not None and ba.get("cost_per_success") is not None:
            cost_mark = "≥ ✗"
        else:
            cost_mark = "unknown ✗"
        w(f"- **{cand_label}** success rate: {fmt(ca['acc_rate'])} vs **{base_label}** "
          f"{fmt(ba['acc_rate'])} → {'≥ ✓' if verdict['succ_ok'] else '< ✗'}")
        w(f"- **{cand_label}** cost/success (metered USD): "
          f"{fmt_money(ca.get('cost_per_success'))} vs **{base_label}** "
          f"{fmt_money(ba.get('cost_per_success'))} → {cost_mark}")
        w(f"- Guardrail (simple_fix wall-clock): {'PASS ✓' if verdict['guardrail_ok'] else 'FAIL ✗'}")
        failed = [name for name, ok in (("success", verdict["succ_ok"]),
                                        ("cost", verdict["cost_ok"]),
                                        ("guardrail", verdict["guardrail_ok"]))
                  if not ok]
        reason = "all gates pass" if not failed else f"failed: {', '.join(failed)}"
        w(f"- **Verdict: {'ADOPTED' if verdict['adopted'] else 'NOT ADOPTED'}** "
          f"{cand_label} ({reason})")
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
        w(f"| {label_for(cfg)} | {a.get('first_pass',0)} | {a.get('first_total',0)} | "
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
        w(f"| {label_for(cfg)} | {a.get('hidden_pass',0)} | {a['n']} | "
          f"{fmt(a.get('hidden_rate'))} |")
    w("")

    # ── raw table ──
    w("## Raw run table")
    w("")
    w("| Case | Config | Rep | Set | Wall(s) | Exit | Timeout | Acc | Hidden | Inv | "
      "Tokens | Cost ($) |")
    w("|------|--------|-----|-----|---------|------|---------|-----|--------|-----|"
      "--------|----------|")
    for r in sorted(results, key=lambda x: (x["case_id"], x["config"], x.get("rep", 0))):
        w(f"| {r['case_id']} | {label_for(r['config'])} | {r.get('rep','')} | "
          f"{r.get('set','')} | "
          f"{fmt(r.get('wall_clock_s'))} | {r.get('agent_exit','')} | "
          f"{'Y' if r.get('timed_out') else 'N'} | "
          f"{'✓' if r.get('acceptance_pass') else '✗'} | "
          f"{'✓' if r.get('hidden_pass') else '✗'} | "
          f"{'✓' if r.get('invariants_pass') else '✗'} | "
          f"{r.get('est_tokens','')} | {fmt_money(r.get('total_cost_usd'))} |")
    w("")

    return "\n".join(lines)


def main():
    # Force UTF-8 on stdout (Windows console defaults to cp1252). Done here, not
    # at import time, so importing this module cannot hijack a caller's stdout.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    results = load_results()
    if not results:
        print("No results found in results.jsonl")
        sys.exit(1)
    blinding = load_blinding()
    index, present = load_metered_index()
    info = join_costs(results, index, present)
    scorecard = generate_scorecard(results, blinding, info)
    with open(SCORECARD_PATH, "w", encoding="utf-8") as f:
        f.write(scorecard)
    print(f"Scorecard written to {SCORECARD_PATH}")
    print(f"\n{scorecard}")


if __name__ == "__main__":
    main()
