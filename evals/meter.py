#!/usr/bin/env python3
"""Real token/cost metering for the Kimi Code eval harness.

Reads eval run records from results.jsonl, matches each to Kimi Code CLI
sessions by workDir + time window, extracts usage.record events from
wire.jsonl, and computes real costs from pricing.yaml.

Timezone handling:
  - started_at in results.jsonl is local naive time (from datetime.now()).
  - createdAt in state.json is UTC with a trailing Z.
  - We convert both to POSIX timestamps and compare in that space.  For the
    naive local string we use datetime.timestamp(), which assumes the
    machine's local timezone — the same timezone run_eval.py used when it
    recorded started_at.  For the UTC string we attach tzinfo=utc.  This is
    robust as long as meter.py runs on the same machine (or same TZ) as the
    eval runner.

Usage:
    python evals/meter.py [--results evals/results.jsonl]
                          [--sessions <sessions-root>]
                          [--out evals/results-metered.jsonl]
                          [--set showcase]

    python evals/meter.py --rebuild-watch <hours> [--sessions <sessions-root>]
                          [--rebuild-turns N]

--rebuild-watch is a separate mode (no results.jsonl needed, no --out write):
for sessions created within the last <hours> hours it reports the first-N-turn
(N default 3) inputCacheCreation vs inputCacheRead sums per session — the
cold-start premium paid every time a new leader session is opened (plan v2,
F9 session_rebuild token class). Read-only; no live API calls.
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RESULTS = os.path.join(SCRIPT_DIR, "results.jsonl")
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "results-metered.jsonl")
DEFAULT_SESSIONS = os.path.expanduser(r"~\.kimi-code\sessions")
PRICING_PATH = os.path.join(SCRIPT_DIR, "pricing.yaml")

PRE_START_MARGIN_S = 120   # session may be created up to 120s before started_at
POST_END_MARGIN_S = 300    # …or up to 300s after the run ended


# ── Pricing YAML parser (regex, no YAML dependency) ───────────────────

def load_pricing(path):
    r"""Parse the JSON-compatible inline-map YAML used by pricing.yaml.

    Expected per-line format (comments and blanks skipped):
        model_name: { input: 3.00, cached_input: 0.30, output: 15.00 }
    """
    pricing = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = re.match(r"^([\w/.-]+):\s*\{(.+)\}\s*$", stripped)
            if not m:
                continue
            model = m.group(1)
            fields = {}
            for part in m.group(2).split(","):
                kv = part.split(":")
                if len(kv) == 2:
                    fields[kv[0].strip()] = float(kv[1].strip())
            if {"input", "cached_input", "output"} <= fields.keys():
                pricing[model] = fields
    return pricing


# ── Path & time utilities ─────────────────────────────────────────────

def norm_path(p):
    """Normalise case and separators for Windows path comparison."""
    return os.path.normcase(os.path.normpath(p))


def local_naive_to_posix(s):
    """Local naive ISO string -> POSIX timestamp (uses machine TZ)."""
    return datetime.fromisoformat(s).timestamp()


def utc_iso_to_posix(s):
    """UTC ISO string with trailing Z -> POSIX timestamp.

    Also accepts an int/float epoch (seconds or milliseconds).
    """
    if isinstance(s, (int, float)):
        # Heuristic: values > 1e12 are milliseconds.
        return s / 1000 if s > 1e12 else s
    s = s.rstrip("Z")
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=timezone.utc).timestamp()


# ── Session indexing ──────────────────────────────────────────────────

def index_sessions(sessions_root):
    """Walk every session_*/state.json under *sessions_root*.

    Returns a list of ``(norm_workdir, posix_created_at, session_dir)`` tuples.
    """
    index = []
    if not os.path.isdir(sessions_root):
        return index
    for wd_dir_name in os.listdir(sessions_root):
        wd_dir = os.path.join(sessions_root, wd_dir_name)
        if not os.path.isdir(wd_dir):
            continue
        for sess_name in os.listdir(wd_dir):
            sess_dir = os.path.join(wd_dir, sess_name)
            state_path = os.path.join(sess_dir, "state.json")
            if not os.path.isfile(state_path):
                continue
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            work_dir = state.get("workDir", "")
            created_at = state.get("createdAt", "")
            if not work_dir or not created_at:
                continue
            try:
                posix_created = utc_iso_to_posix(created_at)
            except (ValueError, TypeError):
                continue
            index.append((norm_path(work_dir), posix_created, sess_dir))
    return index


# ── Usage extraction ──────────────────────────────────────────────────

def parse_session_usage(session_dir):
    """Sum ``usage.record`` events across all ``agents/*/wire.jsonl`` files.

    Only ``usage.record`` events are counted — ``step.end`` and other event
    types also carry usage data, but including them would double-count.

    Returns ``{model: {inputOther, output, inputCacheRead, inputCacheCreation}}``.
    """
    agents_dir = os.path.join(session_dir, "agents")
    if not os.path.isdir(agents_dir):
        return {}

    totals = {}
    for agent_name in sorted(os.listdir(agents_dir)):
        wire_path = os.path.join(agents_dir, agent_name, "wire.jsonl")
        if not os.path.isfile(wire_path):
            continue
        try:
            with open(wire_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("type") != "usage.record":
                        continue
                    model = evt.get("model", "")
                    if not model:
                        continue
                    usage = evt.get("usage", {})
                    if model not in totals:
                        totals[model] = {
                            "inputOther": 0,
                            "output": 0,
                            "inputCacheRead": 0,
                            "inputCacheCreation": 0,
                        }
                    for k in totals[model]:
                        totals[model][k] += usage.get(k, 0)
        except OSError:
            continue
    return totals


# ── session_rebuild token class (cold-start premium, plan v2 F9) ──────

REBUILD_WATCH_TURNS = 3


def session_rebuild_usage(session_dir, n=REBUILD_WATCH_TURNS):
    """First-N ``usage.record`` cache sums per agent in one session.

    A fresh session pays inputCacheCreation on its opening turns before the
    prompt cache warms; a resumed session reads instead. The creation/read
    split over the first N turns is the cold-start premium.

    Returns ``{agent: {"turns", "inputCacheCreation", "inputCacheRead"}}``.
    """
    agents_dir = os.path.join(session_dir, "agents")
    per_agent = {}
    if not os.path.isdir(agents_dir):
        return per_agent
    for agent_name in sorted(os.listdir(agents_dir)):
        wire_path = os.path.join(agents_dir, agent_name, "wire.jsonl")
        if not os.path.isfile(wire_path):
            continue
        turns = 0
        create = read = 0
        try:
            with open(wire_path, "r", encoding="utf-8") as f:
                for line in f:
                    if turns >= n:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("type") != "usage.record":
                        continue
                    usage = evt.get("usage", {})
                    create += usage.get("inputCacheCreation", 0)
                    read += usage.get("inputCacheRead", 0)
                    turns += 1
        except OSError:
            continue
        per_agent[agent_name] = {
            "turns": turns,
            "inputCacheCreation": create,
            "inputCacheRead": read,
        }
    return per_agent


def run_rebuild_watch(sessions_root, window_hours, n=REBUILD_WATCH_TURNS):
    """Report first-N-turn cacheCreate/cacheRead for recently created sessions."""
    cutoff = time.time() - window_hours * 3600
    rows = []
    for wd_norm, created_posix, sess_dir in index_sessions(sessions_root):
        if created_posix < cutoff:
            continue
        per_agent = session_rebuild_usage(sess_dir, n)
        rows.append({
            "session": os.path.basename(sess_dir),
            "workDir": wd_norm,
            "created_at": datetime.fromtimestamp(
                created_posix, tz=timezone.utc).isoformat(),
            "agents": per_agent,
            "inputCacheCreation": sum(a["inputCacheCreation"]
                                      for a in per_agent.values()),
            "inputCacheRead": sum(a["inputCacheRead"]
                                  for a in per_agent.values()),
        })
    rows.sort(key=lambda r: r["created_at"])

    print(f"\nSESSION REBUILD WATCH — sessions created in the last "
          f"{window_hours:g}h; first {n} usage.record turn(s) per agent")
    print("-" * 90)
    hdr = f"{'Session':<40} {'Created (UTC)':<25} {'cacheCreate':>12} {'cacheRead':>12}"
    print(hdr)
    print("-" * 90)
    for r in rows:
        print(f"{r['session']:<40} {r['created_at']:<25} "
              f"{r['inputCacheCreation']:>12,} {r['inputCacheRead']:>12,}")
        for agent, a in sorted(r["agents"].items()):
            print(f"  {'[' + agent + ']':<38} {a['turns']:>3} turn(s) "
                  f"{a['inputCacheCreation']:>17,} {a['inputCacheRead']:>12,}")
    print("-" * 90)
    tot_c = sum(r["inputCacheCreation"] for r in rows)
    tot_r = sum(r["inputCacheRead"] for r in rows)
    print(f"{len(rows)} session(s): total first-{n}-turns "
          f"inputCacheCreation={tot_c:,}  inputCacheRead={tot_r:,}")
    print("\nJSON:")
    print(json.dumps(rows, indent=2))


# ── Cost calculation ──────────────────────────────────────────────────

def compute_model_cost(model, tokens, pricing):
    """USD cost for one model's token breakdown, or ``None`` if unknown.

    Formula (per 1M-token prices):
        inputOther/1M * input  +  inputCacheCreation/1M * input
      + inputCacheRead/1M * cached_input  +  output/1M * output
    """
    p = pricing.get(model)
    if p is None:
        return None
    cost = (
        tokens["inputOther"] / 1e6 * p["input"]
        + tokens["inputCacheCreation"] / 1e6 * p["input"]
        + tokens["inputCacheRead"] / 1e6 * p["cached_input"]
        + tokens["output"] / 1e6 * p["output"]
    )
    return round(cost, 6)


def total_tokens_for_model(tokens):
    return sum(tokens.values())


# ── Record metering ───────────────────────────────────────────────────

def find_matching_sessions(record, session_index):
    """Return session dirs whose workDir == run_dir and createdAt is in-window."""
    run_dir_norm = norm_path(record["run_dir"])
    started_posix = local_naive_to_posix(record["started_at"])
    wall = record.get("wall_clock_s", 0)
    lo = started_posix - PRE_START_MARGIN_S
    hi = started_posix + wall + POST_END_MARGIN_S

    matched = []
    for wd_norm, created_posix, sess_dir in session_index:
        if wd_norm != run_dir_norm:
            continue
        if lo <= created_posix <= hi:
            matched.append(sess_dir)
    return matched


def meter_record(record, session_index, pricing, warnings):
    """Enrich a single run record with token/cost data."""
    matched = find_matching_sessions(record, session_index)

    if not matched:
        warnings.append(
            f"no session matched  {record['case_id']}/{record['config']}/{record['rep']}  "
            f"run_dir={record['run_dir']}  started_at={record['started_at']}"
        )
        out = dict(record)
        out.update(
            metered=False, sessions_matched=0,
            tokens_by_model={}, cost_usd_by_model={}, total_cost_usd=None,
        )
        return out

    # Aggregate usage across all matching sessions (leader + workers).
    agg = {}
    for sess_dir in matched:
        for model, toks in parse_session_usage(sess_dir).items():
            if model not in agg:
                agg[model] = {"inputOther": 0, "output": 0,
                              "inputCacheRead": 0, "inputCacheCreation": 0}
            for k in agg[model]:
                agg[model][k] += toks.get(k, 0)

    if not agg:
        warnings.append(
            f"no usage.record events  {record['case_id']}/{record['config']}/{record['rep']}  "
            f"({len(matched)} session(s) matched but 0 usage records)"
        )

    cost_by_model = {}
    has_unknown = False
    for model, toks in agg.items():
        c = compute_model_cost(model, toks, pricing)
        if c is None:
            warnings.append(
                f"unknown model '{model}'  {record['case_id']}/{record['config']}/{record['rep']}  "
                f"— cost set to null"
            )
            has_unknown = True
        cost_by_model[model] = c

    total_cost = None if has_unknown else round(
        sum(v for v in cost_by_model.values() if v is not None), 6
    )

    out = dict(record)
    out.update(
        metered=True,
        sessions_matched=len(matched),
        tokens_by_model=agg,
        cost_usd_by_model=cost_by_model,
        total_cost_usd=total_cost,
    )
    return out


# ── I/O ───────────────────────────────────────────────────────────────

def load_results(path):
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def write_metered(records, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── Reporting ─────────────────────────────────────────────────────────

def median_safe(values):
    valid = [v for v in values if v is not None]
    return statistics.median(valid) if valid else None


def _cost(v):
    return "  N/A " if v is None else f"${v:,.4f}"


def _tok(v):
    return "N/A" if v is None else f"{v:,.0f}"


def print_comparison_table(records):
    metered = [r for r in records if r.get("metered")]
    unmetered = [r for r in records if not r.get("metered")]
    configs = sorted(set(r["config"] for r in records))

    # ── by-config summary ──
    print("\n" + "=" * 90)
    print("COST COMPARISON BY CONFIG")
    print("=" * 90)

    hdr = f"{'Cfg':<4} {'N':>5}  {'Med $/run':>10}  {'Med tokens':>12}  Per-model token breakdown (median)"
    print(hdr)
    print("-" * 90)

    for cfg in configs:
        cfg_recs = [r for r in metered if r["config"] == cfg]
        costs = [r["total_cost_usd"] for r in cfg_recs]
        toks = [sum(sum(tv.values()) for tv in r["tokens_by_model"].values())
                for r in cfg_recs]
        med_c = median_safe(costs)
        med_t = median_safe(toks)

        all_models = set()
        for r in cfg_recs:
            all_models.update(r["tokens_by_model"].keys())

        parts = []
        for model in sorted(all_models):
            mt = []
            for r in cfg_recs:
                if model in r["tokens_by_model"]:
                    mt.append(total_tokens_for_model(r["tokens_by_model"][model]))
            parts.append(f"{model.split('/')[-1]}: {_tok(median_safe(mt))}")
        breakdown = ", ".join(parts) if parts else "—"

        n_tot = len([r for r in records if r["config"] == cfg])
        print(f"{cfg:<4} {len(cfg_recs):>2}/{n_tot:<2}  {_cost(med_c):>10}  {_tok(med_t):>12}  {breakdown}")

    # ── per-case × config matrix ──
    cases = sorted(set(r["case_id"] for r in records))
    print("\n" + "=" * 90)
    print("PER-CASE × CONFIG COST MATRIX  (median $total_cost_usd across reps)")
    print("=" * 90)

    cw = 12
    kw = max(max(len(c) for c in cases), 4) if cases else 4
    hdr = f"{'Case':<{kw}}"
    for cfg in configs:
        hdr += f" | {cfg:>{cw}}"
    print(hdr)
    print("-" * len(hdr))

    for cid in cases:
        row = f"{cid:<{kw}}"
        for cfg in configs:
            cell = [r for r in metered
                    if r["case_id"] == cid and r["config"] == cfg]
            row += f" | {_cost(median_safe([r['total_cost_usd'] for r in cell])):>{cw}}"
        print(row)

    # ── unmatched ──
    if unmetered:
        print("\n" + "=" * 90)
        print("UNMATCHED RECORDS (metered: false)")
        print("=" * 90)
        for r in unmetered:
            print(f"  {r['case_id']}/{r['config']}/{r['rep']}  "
                  f"started_at={r['started_at']}  run_dir={r['run_dir']}")

    print()


# ── Main ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Real token/cost metering for Kimi Code eval harness.")
    ap.add_argument("--results", default=DEFAULT_RESULTS,
                    help=f"Path to results.jsonl (default: {DEFAULT_RESULTS})")
    ap.add_argument("--sessions", default=DEFAULT_SESSIONS,
                    help=f"Sessions root (default: {DEFAULT_SESSIONS})")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help=f"Output JSONL (default: {DEFAULT_OUT})")
    ap.add_argument("--set", default=None,
                    help="Filter by set (e.g. showcase, tune, holdout)")
    ap.add_argument("--rebuild-watch", type=float, default=None, metavar="HOURS",
                    help="Rebuild-watch mode: report first-N-turn cacheCreate/"
                         "cacheRead for sessions created in the last HOURS hours")
    ap.add_argument("--rebuild-turns", type=int, default=REBUILD_WATCH_TURNS,
                    help=f"Turns per session for rebuild watch (default {REBUILD_WATCH_TURNS})")
    args = ap.parse_args()

    if args.rebuild_watch is not None:
        run_rebuild_watch(args.sessions, args.rebuild_watch, args.rebuild_turns)
        return

    pricing = load_pricing(PRICING_PATH)
    print(f"Loaded pricing for {len(pricing)} models: "
          f"{', '.join(sorted(pricing.keys()))}")

    records = load_results(args.results)
    if args.set:
        records = [r for r in records if r.get("set") == args.set]
    print(f"Loaded {len(records)} records"
          + (f" (set={args.set})" if args.set else ""))

    print(f"Indexing sessions under {args.sessions} …")
    session_index = index_sessions(args.sessions)
    print(f"Indexed {len(session_index)} sessions")

    warnings = []
    metered_records = []
    for rec in records:
        metered_records.append(
            meter_record(rec, session_index, pricing, warnings))

    if warnings:
        print(f"\n{'!' * 70}")
        print(f"{len(warnings)} WARNING(S):")
        for w in warnings:
            print(f"  {w}")
        print()

    write_metered(metered_records, args.out)
    print(f"Wrote {len(metered_records)} metered records to {args.out}")

    print_comparison_table(metered_records)

    n_ok = sum(1 for r in metered_records if r.get("metered"))
    print(f"Summary: {n_ok} metered, {len(metered_records) - n_ok} unmatched")


if __name__ == "__main__":
    main()
