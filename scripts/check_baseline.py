#!/usr/bin/env python3
"""Fail CI when a suite runs fewer tests, gains skips, or runs none.

    python scripts/check_baseline.py --baseline docs/baseline/baseline.json --current current.json

Both files map suite -> {"ran": int, "skipped": int, "failed": int}. The baseline
only changes by a reviewed commit (CODEOWNERS); lowering it is a deliberate act.
"""
import argparse
import json
import sys


def compare(baseline, current):
    """Violations of `current` against `baseline`, one string each; [] when clean."""
    out = []
    for suite, base in sorted(baseline.items()):
        cur = current.get(suite)
        if not cur or cur.get("ran", 0) == 0:
            out.append(f"{suite}: no tests")
            continue
        if cur["ran"] < base["ran"]:
            out.append(f"{suite}: ran {cur['ran']} < baseline {base['ran']}")
        if cur.get("skipped", 0) > base.get("skipped", 0):
            out.append(f"{suite}: skipped {cur['skipped']} > baseline {base['skipped']}")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--baseline", required=True)
    p.add_argument("--current", required=True)
    args = p.parse_args(argv)
    with open(args.baseline, encoding="utf-8") as f:
        baseline = json.load(f)
    with open(args.current, encoding="utf-8") as f:
        current = json.load(f)
    violations = compare(baseline, current)
    for v in violations:
        print(v)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
