#!/usr/bin/env python3
"""extract_log — deterministic fact extractor for kimi session wire.jsonl files.

Facts that exist as structured records (session logs, CI logs, audit trails)
are extracted mechanically FIRST; LLMs interpret extractions, never scan raw
volume (plan v2, F1). This extractor is verifier-class: hash-frozen per goal —
changes force re-review.

Per input file it emits a facts listing:
  - tool calls (name, turn/step, args summary — command for shell tools,
    path for file tools), flagged as file writes/edits or gh/git commands
  - assistant text lengths (per content part; thinking parts counted
    separately)
  - timestamps (epoch ms) on every fact row

…plus a coverage manifest: bytes_in, lines_total, records_parsed,
records_unrecognized (with counts by unknown type), malformed lines, and
skip/truncation flags. Leaders must reject extractions whose coverage shows
nonzero unrecognized records above a small threshold.

Usage:
    python scripts/extract_log.py <wire.jsonl...> --out <dir>

Writes <dir>/<file-key>.facts.json per input and <dir>/manifest.json.
Deterministic: identical inputs produce byte-identical outputs (input paths
are processed in sorted order; no wall-clock, no environment dependence).

Stdlib only, Python 3.10.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

# Top-level wire.jsonl event types this extractor understands (enumerated from
# live sessions, 2026-08-14). Anything else is counted in the coverage manifest
# (never silently dropped).
KNOWN_TOP_LEVEL = frozenset({
    "metadata",
    "config.update",
    "context.append_message",
    "context.append_loop_event",
    "context.apply_compaction",
    "full_compaction.begin",
    "full_compaction.cancel",
    "full_compaction.complete",
    "goal.clear",
    "goal.create",
    "goal.update",
    "interaction.request",
    "interaction.resolved",
    "llm.request",
    "llm.tools_snapshot",
    "mcp.tools_discovered",
    "permission.record_approval_result",
    "permission.set_mode",
    "plan.revision",
    "plan_mode.cancel",
    "plan_mode.enter",
    "plan_mode.exit",
    "profile.bind",
    "step.end",
    "swarm_mode.enter",
    "swarm_mode.exit",
    "task.started",
    "task.terminated",
    "tools.set_active_tools",
    "tools.update_store",
    "turn.cancel",
    "turn.ended",
    "turn.prompt",
    "turn.steer",
    "usage.record",
    # Added from a live capture on 2026-08-27 (TOOL-023, issue #53). The
    # enumeration above was frozen 2026-08-14; against kimi 0.34.0 these six
    # accounted for 33 of 143 records -- 23% -- in one ordinary session. All
    # of them were landing in records_unrecognized, so by this module's own
    # contract ("leaders must reject extractions whose coverage shows nonzero
    # unrecognized records") every current extraction was rejectable, and
    # nobody was looking. Refresh from evals/fixtures/wire/ on kimi-CLI
    # version bumps -- see #3 and #54.
    "plugin.session_start",
    "prompt.accepted",
    "runtime.set_binding",
    "staleGuard.recorded",
    "token_counting.measured",
    "token_counting.turn_recorded",
})

# Inner event types recognized inside context.append_loop_event.
KNOWN_LOOP_EVENTS = frozenset({
    "step.begin", "step.end", "content.part", "tool.call", "tool.result",
})

# Tool names whose primary argument is a filesystem path (write/edit class).
_WRITE_TOOLS = {"Write"}
_EDIT_TOOLS = {"Edit"}

# Shell tools whose command argument is summarized and scanned for gh/git.
_SHELL_TOOLS = {"Bash", "Shell"}

_SUMMARY_MAX_CHARS = 200


def _summarize_args(name, args):
    """Short deterministic args summary for one tool call."""
    if not isinstance(args, dict):
        return ""
    if name in _SHELL_TOOLS:
        return str(args.get("command", ""))[:_SUMMARY_MAX_CHARS]
    for key in ("path", "file_path"):
        if isinstance(args.get(key), str):
            return args[key]
    # Fallback: first string value, bounded.
    for key in sorted(args):
        if isinstance(args[key], str):
            return f"{key}={args[key]}"[:_SUMMARY_MAX_CHARS]
    return ""


def _gh_git_command(name, args):
    """True when a shell tool call invokes git or gh."""
    if name not in _SHELL_TOOLS or not isinstance(args, dict):
        return False
    command = str(args.get("command", ""))
    # Match git/gh as the first word of any command segment.
    for segment in command.replace(";", "\n").replace("&&", "\n").replace("||", "\n").splitlines():
        word = segment.strip().split(" ", 1)[0] if segment.strip() else ""
        if word in ("git", "gh", "git.exe", "gh.exe"):
            return True
    return False


def _file_write_or_edit(name, args):
    """'write' | 'edit' | None for file-mutating tool calls."""
    if name in _WRITE_TOOLS:
        return "write"
    if name in _EDIT_TOOLS:
        return "edit"
    return None


def extract_file(path):
    """Parse one wire.jsonl file. Returns (facts_dict, coverage_dict)."""
    raw = Path(path).read_bytes()
    coverage = {
        "bytes_in": len(raw),
        "lines_total": 0,
        "records_parsed": 0,
        "records_unrecognized": 0,
        "unrecognized_types": {},
        "malformed_lines": 0,
        "blank_lines_skipped": 0,
        # Reserved flags — this extractor never truncates input; the flags
        # exist so a coverage contract breach is representable.
        "truncated": False,
        "skipped": False,
    }
    facts = {
        "source": str(path),
        "tool_calls": [],
        "file_mutations": [],
        "gh_git_commands": [],
        "assistant_text_lengths": [],
        "usage_records": 0,
    }

    for line in raw.decode("utf-8", errors="replace").splitlines():
        coverage["lines_total"] += 1
        stripped = line.strip()
        if not stripped:
            coverage["blank_lines_skipped"] += 1
            continue
        try:
            evt = json.loads(stripped)
        except json.JSONDecodeError:
            coverage["malformed_lines"] += 1
            continue
        if not isinstance(evt, dict):
            coverage["malformed_lines"] += 1
            continue
        etype = evt.get("type")
        timestamp = evt.get("time")
        if etype not in KNOWN_TOP_LEVEL:
            coverage["records_unrecognized"] += 1
            key = str(etype)
            coverage["unrecognized_types"][key] = \
                coverage["unrecognized_types"].get(key, 0) + 1
            continue
        coverage["records_parsed"] += 1

        if etype == "usage.record":
            facts["usage_records"] += 1
            continue
        if etype != "context.append_loop_event":
            continue
        inner = evt.get("event")
        if not isinstance(inner, dict):
            continue
        itype = inner.get("type")
        if itype not in KNOWN_LOOP_EVENTS:
            coverage["records_unrecognized"] += 1
            key = f"loop:{itype}"
            coverage["unrecognized_types"][key] = \
                coverage["unrecognized_types"].get(key, 0) + 1
            continue

        if itype == "tool.call":
            name = inner.get("name")
            args = inner.get("args")
            row = {
                "time": timestamp,
                "turn": inner.get("turnId"),
                "step": inner.get("step"),
                "name": name,
                "summary": _summarize_args(name, args),
            }
            facts["tool_calls"].append(row)
            mutation = _file_write_or_edit(name, args)
            if mutation is not None:
                path_arg = args.get("path") if isinstance(args, dict) else None
                facts["file_mutations"].append({
                    "time": timestamp,
                    "turn": inner.get("turnId"),
                    "kind": mutation,
                    "path": path_arg,
                })
            if _gh_git_command(name, args):
                facts["gh_git_commands"].append({
                    "time": timestamp,
                    "turn": inner.get("turnId"),
                    "command": str(args.get("command", ""))[:_SUMMARY_MAX_CHARS],
                })
        elif itype == "content.part":
            part = inner.get("part")
            if isinstance(part, dict) and part.get("type") == "text":
                facts["assistant_text_lengths"].append({
                    "time": timestamp,
                    "turn": inner.get("turnId"),
                    "chars": len(str(part.get("text", ""))),
                })

    return facts, coverage


def _file_key(path):
    """Collision-free output key: basename + short hash of the full path."""
    p = Path(path)
    digest = hashlib.sha256(str(p).encode("utf-8")).hexdigest()[:8]
    return f"{p.stem}-{digest}"


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="extract_log",
        description="Deterministic fact extraction from kimi wire.jsonl files, "
                    "with a coverage manifest.",
    )
    parser.add_argument("wire_files", nargs="+", help="wire.jsonl file(s)")
    parser.add_argument("--out", required=True, help="output directory")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {"files": []}
    for wire in sorted(args.wire_files):
        p = Path(wire)
        if not p.is_file():
            manifest["files"].append({
                "source": str(p), "error": "not_a_file",
            })
            continue
        facts, coverage = extract_file(p)
        key = _file_key(p)
        facts_path = out_dir / f"{key}.facts.json"
        payload = json.dumps(facts, indent=2, sort_keys=True) + "\n"
        facts_path.write_text(payload, encoding="utf-8")
        manifest["files"].append({
            "source": str(p),
            "facts_file": facts_path.name,
            "coverage": coverage,
        })

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(manifest['files'])} file entrie(s) + manifest to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
