#!/usr/bin/env python3
"""wirewatch — batch escalation evaluator over kimi session wires.

Layer 1 of the 2026-10-29 escalation redesign. The runner journal only sees
dispatch-path work; the tower and native pods never touch it. The session
``wire.jsonl`` records everything, so the escalation triggers read that instead.

This shell does the I/O the pure core refuses to do: parse wires through
``scripts/extract_log.py`` (the one wire parser — never a second one), join an
optional runner journal, run ``core/wire_triggers.evaluate``, and write:

    <out>/escalations.jsonl   one finding per line (lane_switch/stop/flag)
    <out>/summary.json        counts, per-wire coverage, and the named
                              unmeasurable triggers

Exit 1 when any stop-severity finding fires.

TOOL-014: ``--out`` must lie OUTSIDE the proctored workspace (the current
working directory) and outside every input wire's session directory. A worker
that can rewrite the finder's output can suppress its own escalation.

Usage:
    python harnesses/kimi-code/wirewatch/evaluator.py <wire.jsonl...> \
        [--journal <runner journal>] --out <dir>

Read-only w.r.t. the wires themselves; deterministic — identical inputs give
byte-identical output files.
"""

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "scripts"))

import extract_log  # noqa: E402
import wire_triggers  # noqa: E402

ESCALATION_SEVERITIES = ("lane_switch", "stop", "flag")
# The extractor's own contract: a leader rejects an extraction whose coverage
# shows unrecognized records. Reported, never silently dropped.
COVERAGE_REJECT_THRESHOLD = 0


def _inside(child, parent):
    child = Path(child).resolve()
    parent = Path(parent).resolve()
    return child == parent or parent in child.parents


def check_out_dir(out_dir, wire_paths, cwd=None):
    """Refuse an output directory the proctored work could rewrite (TOOL-014)."""
    out = Path(out_dir).expanduser().resolve()
    workspace = Path(cwd or os.getcwd()).resolve()
    if _inside(out, workspace):
        raise SystemExit(_error(
            "out_dir_inside_workspace",
            "escalation findings must not be writable by the work they judge; "
            "choose --out outside the workspace",
            out_dir=str(out), workspace=str(workspace)))
    for wire in wire_paths:
        # A wire's session dir is CLI-owned and append-only; the evaluator must
        # not nest its state inside the surface it reads.
        session_dir = Path(wire).resolve().parent
        if _inside(out, session_dir):
            raise SystemExit(_error(
                "out_dir_inside_session",
                "the session wire directory is CLI-owned; --out must live "
                "elsewhere",
                out_dir=str(out), session_dir=str(session_dir)))
    return out


def _error(kind, detail, **evidence):
    return json.dumps({"error": kind, "detail": detail, **evidence},
                      sort_keys=True)


def evaluate_paths(wire_paths, journal_path=None, cfg=None):
    """Return (findings, per_wire) for a list of wire paths.

    With more than one wire, T8 is evaluated ONCE over the union of all
    commands and the per-wire T8 rows are dropped: branch generations belong to
    the repository, so a session that created four branches on one ticket
    across four agent wires must not read as four separate quiet wires.
    """
    journal = None
    if journal_path:
        journal = wire_triggers.load_journal(journal_path)
    cfg = cfg or wire_triggers.DEFAULTS
    paths = sorted(str(p) for p in wire_paths)
    findings = []
    per_wire = []
    all_facts = []
    for wire in paths:
        facts, coverage = extract_log.extract_file(wire)
        all_facts.append(facts)
        rows = wire_triggers.evaluate(facts, journal=journal, cfg=cfg)
        for row in rows:
            row = dict(row)
            row["wire"] = facts["source"]
            findings.append(row)
        per_wire.append({
            "wire": facts["source"],
            "coverage": coverage,
            "coverage_rejectable":
                coverage["records_unrecognized"] > COVERAGE_REJECT_THRESHOLD,
            "findings": len(rows),
            "unmeasurable": sorted({r["trigger"] for r in rows
                                    if r["severity"] == "unmeasurable"}),
        })
    if len(paths) > 1:
        findings = [f for f in findings
                    if f["trigger"] not in wire_triggers.CROSS_WIRE_TRIGGERS]
        for row in wire_triggers.evaluate_session(all_facts, journal=journal,
                                                  cfg=cfg):
            if row["severity"] == "unmeasurable":
                per_wire.append({
                    "wire": "<session>",
                    "coverage": None,
                    "coverage_rejectable": False,
                    "findings": 1,
                    "unmeasurable": [row["trigger"]],
                })
                continue
            row = dict(row)
            row["wire"] = "<session>"
            findings.append(row)
    findings.sort(key=lambda f: (
        f["at"] if isinstance(f.get("at"), (int, float)) else -1,
        f["trigger"], f.get("wire") or "",
        json.dumps(f["evidence"], sort_keys=True, default=str)))
    return findings, per_wire


def write_outputs(out_dir, findings, per_wire, journal_path=None):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    escalations = [f for f in findings
                   if f["severity"] in ESCALATION_SEVERITIES]
    lines = [json.dumps(f, sort_keys=True, default=str) for f in escalations]
    (out / "escalations.jsonl").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    # Named, never silent — and deduplicated: with 54 subagent wires, one row
    # per wire would bury the finding rather than report it.
    grouped = {}
    for f in findings:
        if f["severity"] != "unmeasurable":
            continue
        key = (f["trigger"], f["evidence"].get("reason"))
        entry = grouped.setdefault(key, {"trigger": f["trigger"],
                                         "reason": f["evidence"].get("reason"),
                                         "wires": 0})
        entry["wires"] += 1
    unmeasurable = [grouped[k] for k in sorted(grouped)]
    summary = {
        "doctrine": "deterministic evidence only; no model output is consumed; "
                    "unmeasurable != passed",
        "journal": str(journal_path) if journal_path else None,
        "wires": per_wire,
        "findings": len(escalations),
        "by_severity": {sev: sum(1 for f in escalations
                                 if f["severity"] == sev)
                        for sev in ESCALATION_SEVERITIES},
        "by_trigger": {trigger: sum(1 for f in escalations
                                    if f["trigger"] == trigger)
                       for trigger in sorted({f["trigger"]
                                              for f in escalations})},
        "unmeasurable": unmeasurable,
        "stops": sum(1 for f in escalations if f["severity"] == "stop"),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="wirewatch",
        description="Deterministic escalation findings over kimi session wires.")
    parser.add_argument("wire_files", nargs="+", help="wire.jsonl file(s)")
    parser.add_argument("--journal", default=None,
                        help="runner journal.jsonl to join (budget evidence)")
    parser.add_argument("--out", required=True,
                        help="output directory, OUTSIDE the workspace")
    parser.add_argument("--cwd-boundary", default=None,
                        help="workspace boundary for the TOOL-014 check "
                             "(default: the process cwd)")
    args = parser.parse_args(argv)

    for wire in args.wire_files:
        if not Path(wire).is_file():
            raise SystemExit(_error("wire_not_a_file", "input wire is missing",
                                    wire=str(wire)))
    if args.journal and not Path(args.journal).is_file():
        raise SystemExit(_error("journal_not_a_file",
                                "journal file is missing",
                                journal=str(args.journal)))
    out = check_out_dir(args.out, args.wire_files, cwd=args.cwd_boundary)

    findings, per_wire = evaluate_paths(args.wire_files, args.journal)
    summary = write_outputs(out, findings, per_wire, args.journal)
    print(json.dumps({"out": str(out), "findings": summary["findings"],
                      "stops": summary["stops"],
                      "unmeasurable": sorted({u["trigger"] for u
                                              in summary["unmeasurable"]})},
                     sort_keys=True))
    return 1 if summary["stops"] else 0


if __name__ == "__main__":
    sys.exit(main())
