#!/usr/bin/env python3
"""redact_wire — turn a real kimi wire.jsonl into a committable schema fixture.

Every cost figure this repo reports comes from wire.jsonl, and nothing in the
suite reads a real one. A committed capture pins the parsing contract so that a
kimi field rename fails loudly instead of silently zeroing every cost (TOOL-023,
issue #53).

Captures come from real operator sessions, so this is WHITELIST-built, not
blacklist-scrubbed: it constructs a new event containing only the fields the
assertions need and discards everything else. Nothing is "cleaned"; anything not
named below simply never reaches the output.

Preserved, and why:
  type          top-level, checked against extract_log.KNOWN_TOP_LEVEL
  event.type    inner, checked against extract_log.KNOWN_LOOP_EVENTS
  event.name    tool name -- an enum from the harness, not user content
  model         must be a key in evals/pricing.yaml
  usage.*       the numeric buckets sum_usage_records accumulates

Dropped: prompts, completions, tool arguments, file paths, session and machine
identifiers, and every other string. Timestamps are replaced with a synthetic
monotonic sequence so the capture does not reveal when the operator worked.

    python evals/fixtures/wire/redact_wire.py <real-wire.jsonl> \\
        --out evals/fixtures/wire/sample-wire.jsonl [--per-type 12]

Refresh on kimi-CLI version bumps -- see #3 and #54. Stdlib only.
"""

import argparse
import json
import sys
from pathlib import Path

# Numeric usage buckets. Kept by name because the whole point of the fixture is
# to fail when one of these is renamed upstream.
USAGE_KEYS = ("inputOther", "output", "inputCacheRead", "inputCacheCreation")

# Synthetic clock: fixed base, one second per event.
BASE_TIME = "2026-01-01T00:00:00"


def _synthetic_time(index):
    h, rem = divmod(index, 3600)
    m, s = divmod(rem, 60)
    return f"2026-01-01T{h % 24:02d}:{m:02d}:{s:02d}"


def redact_event(evt, index):
    """Build a new event from an allowlist. Returns None to drop it."""
    if not isinstance(evt, dict):
        return None
    etype = evt.get("type")
    if not isinstance(etype, str):
        return None

    out = {"type": etype, "time": _synthetic_time(index)}

    if etype == "usage.record":
        model = evt.get("model")
        if isinstance(model, str):
            out["model"] = model
        usage = evt.get("usage")
        if isinstance(usage, dict):
            # Copy only numeric values under the known keys. A key present
            # upstream but absent here would mean kimi renamed it, which is
            # exactly the drift the fixture exists to catch.
            out["usage"] = {k: usage[k] for k in USAGE_KEYS
                            if isinstance(usage.get(k), (int, float))}
        return out

    if etype == "context.append_loop_event":
        inner = evt.get("event")
        if not isinstance(inner, dict):
            return out
        itype = inner.get("type")
        new_inner = {}
        if isinstance(itype, str):
            new_inner["type"] = itype
        name = inner.get("name")
        if isinstance(name, str):
            new_inner["name"] = name
        if "args" in inner:
            # Arguments carry paths, commands and file content. Keep only the
            # fact that arguments existed.
            new_inner["args"] = {"redacted": True}
        out["event"] = new_inner
        return out

    return out


def redact(src, per_type):
    """Redact, keeping every usage.record and at most per_type of each other."""
    kept, seen, stats = [], {}, {"read": 0, "malformed": 0, "dropped": 0}
    for line in Path(src).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        stats["read"] += 1
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            stats["malformed"] += 1
            continue
        etype = evt.get("type") if isinstance(evt, dict) else None
        # usage.record is never capped: the token assertions need real numbers.
        if etype != "usage.record":
            seen[etype] = seen.get(etype, 0) + 1
            if seen[etype] > per_type:
                stats["dropped"] += 1
                continue
        red = redact_event(evt, len(kept))
        if red is None:
            stats["dropped"] += 1
            continue
        kept.append(red)
    return kept, stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", help="a real wire.jsonl to redact")
    ap.add_argument("--out", required=True, help="fixture path to write")
    ap.add_argument("--per-type", type=int, default=12,
                    help="max events kept per non-usage type (default 12)")
    args = ap.parse_args(argv)

    kept, stats = redact(args.source, args.per_type)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for evt in kept:
            f.write(json.dumps(evt, sort_keys=True) + "\n")

    types = sorted({e["type"] for e in kept})
    usage = [e for e in kept if e["type"] == "usage.record"]
    models = sorted({e.get("model", "?") for e in usage})
    print(json.dumps({
        "wrote": str(out),
        "events_in": stats["read"],
        "events_kept": len(kept),
        "dropped": stats["dropped"],
        "malformed_in_source": stats["malformed"],
        "distinct_types": len(types),
        "usage_records": len(usage),
        "models": models,
        "types": types,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
