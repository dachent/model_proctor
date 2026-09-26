#!/usr/bin/env python3
"""catalog.py — the live harness catalog (#96 / TOOL-031).

"start subagent with model [x]" needs a list of [x]. The catalog is kimi's
OWN config ([models.*] keys), read live at invocation — never a synced copy
(#23: a second catalog surface is exactly what desynced on 2026-08-28) —
joined with the pricing table so the leader sees what a dispatch costs
before spending it. Models with no pricing row are flagged: they meter as
unknown (A13), never $0. Retired pricing rows are NOT listed — the catalog
is what can be dispatched NOW.

Read-only; no dispatch, no model calls.

Usage:
  python catalog.py            # human table
  python catalog.py --json     # machine list
"""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import delegate  # noqa: E402


def _load_pricing_fn():
    """runner.load_pricing — one parser, both layouts (flat sibling / repo)."""
    for cand in (_HERE / "runner.py",
                 _HERE.parents[0] / "runner" / "runner.py"):
        if cand.is_file():
            spec = importlib.util.spec_from_file_location("runner_pricing",
                                                         str(cand))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.load_pricing
    return None


def _pricing_path():
    cand = _HERE / "pricing.yaml"
    if cand.is_file():
        return cand
    if len(_HERE.parents) > 2:
        cand = _HERE.parents[2] / "evals" / "pricing.yaml"
        if cand.is_file():
            return cand
    return None


def build_rows():
    """(rows, note). rows are per-model dicts; note is set when the live
    catalog itself cannot be read (which is a failure, not an empty list)."""
    ids, note = delegate.live_harness_models()
    if note:
        return [], note
    load_pricing = _load_pricing_fn()
    pricing = {}
    ppath = _pricing_path()
    if load_pricing and ppath:
        try:
            pricing = load_pricing(str(ppath))
        except OSError:
            pricing = {}
    rows = []
    for mid in sorted(ids):
        p = pricing.get(mid)
        rows.append({
            "model": mid,
            "priced": p is not None,
            "input_per_m": p["input"] if p else None,
            "cached_input_per_m": p["cached_input"] if p else None,
            "output_per_m": p["output"] if p else None,
            "meters_as": "priced" if p is not None else "unknown",
        })
    return rows, None


def main():
    ap = argparse.ArgumentParser(prog="catalog")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable list")
    args = ap.parse_args()

    rows, note = build_rows()
    if note:
        print(f"catalog unavailable: {note}", file=sys.stderr)
        return 2

    ppath = _pricing_path()
    if args.json:
        print(json.dumps({"catalog_size": len(rows),
                          "catalog_source": str(delegate._harness_config_path()),
                          "pricing_source": str(ppath) if ppath else None,
                          "models": rows}, indent=2))
        return 0

    print(f"live harness catalog: {len(rows)} models "
          f"(source: {delegate._harness_config_path()})")
    if ppath:
        print(f"pricing: {ppath}")
    print(f"{'model':44} {'input':>8} {'cached':>8} {'output':>8}")
    for r in rows:
        if r["priced"]:
            print(f"{r['model']:44} {r['input_per_m']:>8} "
                  f"{r['cached_input_per_m']:>8} {r['output_per_m']:>8}")
        else:
            print(f"{r['model']:44} {'UNPRICED':>8}"
                  f"{'':>17}meters as unknown, never $0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
