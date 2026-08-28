#!/usr/bin/env python3
"""skill_trigger — was the proctor actually invoked? (TOOL-026, issue #59)

Every measurement in this repo asks how well the runner behaves once invoked.
None asks whether it GETS invoked. That question sits upstream of all of them:
if the model-proctor skill fires on a fifth of the sessions it should, the
acceptance-gate work matters a fifth as much as its test suites suggest. A
control plane nobody triggers has no effect however good its gates are.

The answer is already on disk. extract_log builds facts["tool_calls"] with the
command string for shell calls, and kimi retains every session's wire.jsonl.
Scanning them costs nothing -- no model runs, no tokens, no network -- and
answers the question over real work rather than synthetic trials.

    python scripts/skill_trigger.py                    # last 30 days
    python scripts/skill_trigger.py --since-days 0     # everything
    python scripts/skill_trigger.py --json

NUMERATOR ONLY -- READ THIS BEFORE QUOTING ANYTHING
---------------------------------------------------
This establishes "the runner was invoked in this session". It CANNOT establish
"it should have been": that means judging whether each session's task was
substantial, which is exactly the discretionary call the skill's description
exists to make.

So there is no denominator, and therefore no rate. Counts and a session list;
the operator supplies the judgement. Reporting a percentage here would invent
the hard half of the measurement -- the same reason PREREG-verifier-error.md
refuses to quote a rate over an empty denominator.

Session logs stay local. This tool is committed; its output is not.
Stdlib only.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import extract_log  # noqa: E402

# Substrings identifying a proctor invocation in a shell command. The installed
# path and the repo-relative path both count; so does bare `runner.py`, since a
# leader may invoke it through a variable or a different prefix.
RUNNER_MARKERS = ("runner.py", "model-proctor")

# Subcommands worth distinguishing. `init` is the tell that a task was actually
# taken through the control plane rather than the binary merely being inspected.
SUBCOMMANDS = ("lane", "init", "dispatch", "verify", "accept", "record",
               "status")

DEFAULT_SINCE_DAYS = 30


def sessions_root(explicit=None):
    if explicit:
        return Path(explicit)
    home = os.environ.get("KIMI_CODE_HOME")
    if home:
        return Path(home) / "sessions"
    return Path(os.environ.get("USERPROFILE", "")) / ".kimi-code" / "sessions"


def find_wires(root, since_days, max_bytes):
    """(wires, skipped) — skipped is itemised, never silent."""
    cutoff = 0 if not since_days else time.time() - since_days * 86400
    # Bytes, not MB: a rounded-to-MB figure reads 0.0 for anything under a
    # megabyte, which makes the field useless exactly when the cap is tight.
    wires, skipped = [], {"too_old": 0, "too_large": 0, "unreadable": 0,
                          "largest_skipped_bytes": 0}
    if not root.is_dir():
        return wires, skipped
    for p in root.rglob("wire.jsonl"):
        try:
            st = p.stat()
        except OSError:
            skipped["unreadable"] += 1
            continue
        if st.st_mtime < cutoff:
            skipped["too_old"] += 1
            continue
        if max_bytes and st.st_size > max_bytes:
            skipped["too_large"] += 1
            skipped["largest_skipped_bytes"] = max(
                skipped["largest_skipped_bytes"], st.st_size)
            continue
        wires.append(p)
    return sorted(wires), skipped


def classify(wire):
    """Return (invoked, subcommands, coverage) for one session log."""
    try:
        facts, coverage = extract_log.extract_file(str(wire))
    except (OSError, ValueError, json.JSONDecodeError):
        return None, [], None
    found = set()
    for call in facts.get("tool_calls") or []:
        summary = str(call.get("summary") or "")
        if not any(m in summary for m in RUNNER_MARKERS):
            continue
        for sub in SUBCOMMANDS:
            # "runner.py init" / "runner.py  init" / installed-path variants.
            if f" {sub}" in summary:
                found.add(sub)
        found.add("*")           # invoked at all, subcommand unclassified
    return (len(found) > 0), sorted(found - {"*"}), coverage


def scan(root, since_days, max_bytes):
    wires, skipped = find_wires(root, since_days, max_bytes)
    sessions, drift = [], {}
    for w in wires:
        invoked, subs, coverage = classify(w)
        if invoked is None:
            skipped["unreadable"] += 1
            continue
        # session dir is <root>/<workdir>/<session_id>/agents/<agent>/wire.jsonl
        try:
            session_id = w.parents[2].name
            workdir = w.parents[3].name
        except IndexError:
            session_id, workdir = w.name, "?"
        sessions.append({"session": session_id, "workdir": workdir,
                         "invoked": invoked, "subcommands": subs})
        if coverage:
            for t, n in (coverage.get("unrecognized_types") or {}).items():
                drift[t] = drift.get(t, 0) + n
    return sessions, skipped, drift


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sessions-root", default=None)
    ap.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS,
                    help="0 scans everything (slow: the corpus can be ~GB)")
    ap.add_argument("--max-bytes", type=int, default=5_000_000,
                    help="skip wire logs larger than this; 0 disables")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = sessions_root(args.sessions_root)
    sessions, skipped, drift = scan(root, args.since_days, args.max_bytes)

    invoked = [s for s in sessions if s["invoked"]]
    with_init = [s for s in invoked if "init" in s["subcommands"]]
    by_workdir = {}
    for s in sessions:
        w = by_workdir.setdefault(s["workdir"], {"sessions": 0, "invoked": 0})
        w["sessions"] += 1
        w["invoked"] += 1 if s["invoked"] else 0

    result = {
        "sessions_root": str(root),
        "sessions_scanned": len(sessions),
        "sessions_invoking_runner": len(invoked),
        "sessions_reaching_init": len(with_init),
        "by_workdir": by_workdir,
        "skipped": skipped,
        "wire_schema_drift": drift,
        "rate": None,
        "rate_note": ("deliberately absent: this counts sessions that DID "
                      "invoke the runner, and cannot judge which SHOULD have. "
                      "There is no denominator, so there is no rate."),
    }

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print(f"sessions root      : {root}")
    print(f"sessions scanned   : {len(sessions)}")
    print(f"invoked the runner : {len(invoked)}")
    print(f"  ...reaching init : {len(with_init)}   "
          "(a task actually taken through the control plane)")
    if skipped["too_old"] or skipped["too_large"] or skipped["unreadable"]:
        print(f"skipped            : {skipped['too_old']} older than the "
              f"window, {skipped['too_large']} over the size cap "
              f"(largest {skipped['largest_skipped_bytes'] / 1e6:.1f} MB), "
              f"{skipped['unreadable']} unreadable")
    if by_workdir:
        print("\nby workdir (invoked / scanned):")
        for w, c in sorted(by_workdir.items(),
                           key=lambda kv: -kv[1]["invoked"])[:15]:
            print(f"  {c['invoked']:>4} / {c['sessions']:<4}  {w}")
    if invoked:
        print("\nsessions that invoked the runner:")
        for s in invoked[:25]:
            subs = ",".join(s["subcommands"]) or "(unclassified)"
            print(f"  {s['session']}  [{subs}]")
        if len(invoked) > 25:
            print(f"  ... and {len(invoked) - 25} more (use --json)")
    if drift:
        print(f"\nwire schema drift across the scan: {drift}")
        print("  extract_log.KNOWN_TOP_LEVEL is behind kimi -- see #53")

    print("\nNO RATE IS REPORTED. This counts sessions that DID invoke the")
    print("runner; judging which SHOULD have is the discretionary call the")
    print("skill description exists to make, so there is no denominator.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
