#!/usr/bin/env python3
"""verifier_error — contingency tables over existing result rows (EVAL-004).

Answers one question the repo has never asked of its own data: how often does
the acceptance verifier pass work that the hidden check rejects?

That number is load-bearing. README Phase 3 cites RouterBench for the
constraint that cascading only beats baseline when verifier/judge error is
<= 0.1, and deteriorates rapidly by 0.2 (arXiv:2403.12031). The constraint has
never been evaluated against this repo's own verifiers.

Reads only committed result files. No model runs, no tokens, no network.

    python evals/verifier_error.py                 # all known files
    python evals/verifier_error.py --file X.jsonl  # one file
    python evals/verifier_error.py --json          # machine-readable

TWO RATES, AND THEY ARE NOT INTERCHANGEABLE
-------------------------------------------
    joint        P(accept AND NOT hidden)  = a_not_h / N
    conditional  P(accept | NOT hidden)    = a_not_h / (rows where hidden failed)

RouterBench's <= 0.1 is a CONDITIONAL misclassification rate. The joint is
dominated by the base rate of bad worker output: if workers are competent it
tends to zero no matter how blind the verifier is. On results.jsonl the two
read 0.25 and 1.00 -- a 4x spread on the same rows. Any figure quoted without
its denominator is meaningless, so this tool always prints both.

WHAT THE ROWS ACTUALLY MEAN (do not pool them)
----------------------------------------------
v1 `results.jsonl` records `acceptance_pass` from run_eval.py, which launches
kimi.exe directly with no runner, no seal and no receipt. It does NOT measure
runner.py's verifier.

v2/v3 rows record `accepted`, which IS the runner's tree-bound acceptance --
but those fixtures are admitted on the rule that a naive solution must pass
check.py and fail hidden_check.py (PREREG-v3.md). The check/hidden gap there is
an authoring criterion, so measuring it measures fixture design.

Neither estimates deployed verifier error on real tasks. Both PREREGs also
record that hidden checks run inside the agent-writable workspace, so
`hidden_pass` is a second fallible instrument, not ground truth.

The tool refuses to pool across acceptance semantics for this reason.
"""

import argparse
import json
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent

# (filename, acceptance field, what that field actually measures)
KNOWN = [
    ("results.jsonl", "acceptance_pass",
     "bare kimi.exe via run_eval.py - no runner, no seal, no receipt"),
    ("pilot-2026-08-25.jsonl", "accepted", "runner tree-bound acceptance"),
    ("phase2-2026-08-25.jsonl", "accepted", "runner tree-bound acceptance"),
    ("phase3-2026-08-25.jsonl", "accepted", "runner tree-bound acceptance"),
    ("phase3-flash0731-2026-08-25.jsonl", "accepted",
     "runner tree-bound acceptance"),
]

# Rule of three: with zero observed events in n trials the 95% upper bound on
# the rate is ~3/n. Resolving a 0.1 threshold therefore needs ~30 rows in the
# conditional denominator before "looks fine" means anything.
MIN_EVENTS_FOR_0_1 = 30


def load(path):
    rows = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"  WARNING: {Path(path).name}:{i} unparseable, skipped",
                  file=sys.stderr)
    return rows


def contingency(rows, accept_field):
    """2x2 over (accepted, hidden_pass). Rows missing either field are counted
    separately rather than silently coerced to False."""
    t = {"a_h": 0, "a_not_h": 0, "not_a_h": 0, "not_a_not_h": 0, "skipped": 0}
    for r in rows:
        if accept_field not in r or "hidden_pass" not in r:
            t["skipped"] += 1
            continue
        a, h = bool(r[accept_field]), bool(r["hidden_pass"])
        key = ("a_" if a else "not_a_") + ("h" if h else "not_h")
        t[key] += 1
    return t


def rates(t):
    n = t["a_h"] + t["a_not_h"] + t["not_a_h"] + t["not_a_not_h"]
    hidden_failures = t["a_not_h"] + t["not_a_not_h"]
    return {
        "n": n,
        "hidden_failures": hidden_failures,
        "joint": round(t["a_not_h"] / n, 4) if n else None,
        "conditional": (round(t["a_not_h"] / hidden_failures, 4)
                        if hidden_failures else None),
    }


def report(name, meaning, t, r):
    print(f"\n{name}")
    print(f"  acceptance field measures: {meaning}")
    print(f"                     hidden_pass=True   hidden_pass=False")
    print(f"  accepted=True       {t['a_h']:>10}   {t['a_not_h']:>17}")
    print(f"  accepted=False      {t['not_a_h']:>10}   {t['not_a_not_h']:>17}")
    if t["skipped"]:
        print(f"  ({t['skipped']} rows skipped: missing a required field)")
    print(f"  n = {r['n']}, rows where the hidden check failed = "
          f"{r['hidden_failures']}")

    j = "n/a" if r["joint"] is None else f"{r['joint']:.4f}"
    print(f"  joint        P(accept AND NOT hidden) = {j}"
          f"   [denominator: all {r['n']} rows]")
    if r["conditional"] is None:
        print("  conditional  P(accept | NOT hidden) = undefined "
              "(the hidden check never failed here)")
    else:
        print(f"  conditional  P(accept | NOT hidden) = {r['conditional']:.4f}"
              f"   [denominator: {r['hidden_failures']} hidden failures]"
              "   <-- the RouterBench quantity")

    if r["hidden_failures"] < MIN_EVENTS_FOR_0_1:
        print(f"  UNDERPOWERED: {r['hidden_failures']} events cannot resolve a "
              f"0.1 threshold (needs ~{MIN_EVENTS_FOR_0_1}). Report the count, "
              "not a verdict.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", action="append", default=None,
                    help="result file to analyse (repeatable); default: all known")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    targets = ([(f, "accepted", "caller-specified") for f in args.file]
               if args.file else KNOWN)

    out = {}
    for name, field, meaning in targets:
        path = EVALS / name if not Path(name).is_absolute() else Path(name)
        if not path.is_file():
            print(f"\n{name}: not found, skipped", file=sys.stderr)
            continue
        rows = load(path)
        # v1 uses acceptance_pass; v2/v3 use accepted. Fall back so an
        # explicitly-passed file still works.
        if rows and field not in rows[0]:
            field = ("acceptance_pass" if "acceptance_pass" in rows[0]
                     else "accepted")
        t = contingency(rows, field)
        r = rates(t)
        out[name] = {"acceptance_field": field, "meaning": meaning,
                     "counts": t, **r}
        if not args.json:
            report(path.name, meaning, t, r)

    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print("\nNOT POOLED ACROSS FILES. v1 acceptance is a bare-CLI check "
              "with no runner;\nv2/v3 acceptance is the runner's tree-bound "
              "receipt. They are different estimands,\nand v2/v3 fixtures are "
              "admitted on a check-passes/hidden-fails rule, so their gap is\n"
              "an authoring criterion rather than a measurement. See "
              "PREREG-verifier-error.md\nfor the decision rule this output is "
              "read against.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
