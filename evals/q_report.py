#!/usr/bin/env python3
"""q_report — first-pass reliability (q) from ledger and eval rows (#96).

The README's routing break-even hinges on q — the probability the cheap tier
completes one unit on the first pass (dispatched once, verified green, no
repair, no escalation, no stagnation). The gated path's ledger IS the
instrument: every task record it appends is a q sample. This tool reads
JSONL rows in either shape the system produces and reports q per source:

- ledger rows (runner `record` → tasks.jsonl): {task_id, lane, dispatches,
  accepted, failures, ...} — first-pass ⟺ dispatches == 1 ∧ accepted ∧
  failures == 0.
- pilot rows (eval evidence): {task_id, arm, attempts: [...], accepted,
  error?, switched_to?} — first-pass ⟺ one attempt ∧ accepted ∧ no error ∧
  no lateral switch.

Usage:
  python evals/q_report.py <rows.jsonl> [<more.jsonl> ...] [--json]

Zero model calls. Stdlib only. The output's meaning is defined in README
"The routing break-even": savings(q) = q − b/s against the strong tier.
"""
import argparse
import json
import sys
from pathlib import Path

USAGE_KEYS = ("inputOther", "output", "inputCacheRead", "inputCacheCreation")


def load_rows(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows


def classify(row):
    """(shape, first_pass, escalated) or (None, None, None) for unknown rows."""
    if "attempts" in row and "accepted" in row:
        # Pilot/eval summary shape.
        attempts = row.get("attempts")
        if not isinstance(attempts, list):
            return None, None, None
        first = (len(attempts) == 1 and bool(row.get("accepted"))
                 and not row.get("error") and not row.get("switched_to"))
        escalated = bool(row.get("switched_to")) or len(attempts) > 1
        return "pilot", first, escalated
    if "dispatches" in row and "accepted" in row:
        # Ledger (tasks.jsonl) shape.
        try:
            dispatches = int(row.get("dispatches") or 0)
            failures = int(row.get("failures") or 0)
        except (TypeError, ValueError):
            return None, None, None
        first = (dispatches == 1 and bool(row.get("accepted"))
                 and failures == 0)
        escalated = dispatches > 1
        return "ledger", first, escalated
    if "envelope_status" in row and "accepted" in row:
        # One-shot dispatch shape (plain/eship drivers): a single delegate
        # call by construction — first-pass is accepted with a completed
        # envelope; there is no escalation dimension.
        first = (bool(row.get("accepted"))
                 and row.get("envelope_status") == "completed")
        return "oneshot", first, False
    return None, None, None


def report(paths):
    per_source = []
    all_first, all_n = [], 0
    for p in paths:
        rows = load_rows(p)
        shapes, firsts, esc = [], [], 0
        for r in rows:
            shape, first, escalated = classify(r)
            if shape is None:
                continue
            shapes.append(shape)
            firsts.append(bool(first))
            if escalated:
                esc += 1
        n = len(firsts)
        n_first = sum(1 for f in firsts if f)
        per_source.append({
            "source": str(p), "rows": n, "shape": (sorted(set(shapes)) or ["?"]),
            "first_pass": n_first,
            "q": (round(n_first / n, 4) if n else None),
            "escalated": esc,
        })
        all_first.extend(firsts)
        all_n += n
    return {
        "per_source": per_source,
        "aggregate": {
            "units": all_n,
            "first_pass": sum(1 for f in all_first if f),
            "q": (round(sum(1 for f in all_first if f) / all_n, 4)
                  if all_n else None),
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="q_report")
    ap.add_argument("sources", nargs="+", help="JSONL row files")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    data = report(args.sources)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print("q_report — first-pass reliability per README 'The routing break-even'")
    print("(first-pass = dispatched once, verified green, no repair/escalation)\n")
    print(f"{'source':44} {'rows':>5} {'first':>6} {'q':>7} {'esc':>5}")
    for s in data["per_source"]:
        q = f"{s['q']:.3f}" if s["q"] is not None else "n/a"
        print(f"{s['source']:44} {s['rows']:>5} {s['first_pass']:>6} "
              f"{q:>7} {s['escalated']:>5}")
    agg = data["aggregate"]
    q = f"{agg['q']:.3f}" if agg["q"] is not None else "n/a"
    print(f"\naggregate: {agg['units']} units, q = {q}")
    if agg["q"] is not None:
        print("savings vs strong tier (README): q - b/s of the comparator's cost")
    return 0


if __name__ == "__main__":
    sys.exit(main())
