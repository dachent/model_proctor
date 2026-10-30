#!/usr/bin/env python3
"""extract_log — deterministic fact extractor for kimi session wire.jsonl files.

Facts that exist as structured records (session logs, CI logs, audit trails)
are extracted mechanically FIRST; LLMs interpret extractions, never scan raw
volume (plan v2, F1). This extractor is verifier-class: hash-frozen per goal —
changes force re-review.

Per input file it emits a facts listing:
  - tool calls (name, turn/step, args summary — command for shell tools,
    path for file tools), flagged as file writes/edits or gh/git commands;
    each call carries its ``tool_call_id`` and the full (bounded) shell
    ``command`` so a failure can be fingerprinted by command
  - tool results: ``exit_code`` (when the wire states one), ``is_error``, and
    the bounded raw output ``tail`` — the error tail a repeated-failure
    fingerprint is built from
  - assistant text parts: length plus a normalized ``digest`` and excerpt
  - owner prompts and steers (``turn.prompt`` / ``turn.steer``): normalized
    ``digest``, excerpt, and whether the text is a system envelope (cron fire,
    task notification, tower wake) rather than the owner speaking
  - turns (start/end/duration) and background tasks (start/end/exit), so
    stagnation and pending-work predicates read records, not wall clocks
  - the model alias of every ``llm.request``
  - timestamps (epoch ms) on every fact row

…plus a coverage manifest: bytes_in, lines_total, records_parsed,
records_unrecognized (with counts by unknown type), malformed lines, and
skip/truncation flags. Leaders must reject extractions whose coverage shows
nonzero unrecognized records above a small threshold.

HASH-FROZEN (verifier-class). ``scripts/extract_log.freeze.json`` records the
sha256 of this file; ``scripts/tests/test_extract_log_freeze.py`` fails when the
two disagree, so every change to this extractor forces a re-review instead of
sliding through. The 2026-10-29 extension (tool results, text digests, owner
prompts, turns, tasks, model aliases; plus 17 top-level event types observed in
live wires that the frozen 2026-08-14/27 enumeration had never seen) is one such
forced re-review — see that manifest's ``reviews`` list.

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
import re
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
    # A second pass on 2026-08-27 over ~869 sessions rather than one capture
    # (TOOL-026, issue #59) found six more. Worth recording why: the first
    # refresh above was taken from a SINGLE session and looked complete
    # because its coverage came back clean. It was not. A one-session sample
    # cannot enumerate a schema -- refresh from a broad scan, not one file.
    "context.undo",
    "cron.add",
    "cron.cursor",
    "cron.delete",
    "task.waitDelivered",
    "token_counting.truncated",
    # A third refresh, 2026-10-29, over the three audited escalation sessions
    # (65f71599 / 6a97dd66 / bbad08a0). These seventeen types carried real
    # traffic in all three (subagent lifecycle in the 53-agent session alone
    # is ~390 records) and every one of them was landing in
    # records_unrecognized -- so the coverage contract above rejected every
    # extraction of the sessions the escalation redesign exists for. Same
    # failure mode as the 2026-08-27 refreshes: a schema enumerated once is
    # complete only until the next capture that disagrees.
    "agent.message.appended",
    "agent.switched",
    "agent.turn.ended",
    "agent.turn.started",
    "changeNotifier.snapshot",
    "context.undone",
    "file_history.checkpoint",
    "file_history.tracked",
    "prompt.aborted",
    "prompt.completed",
    "prompt.steered",
    "subagent.cancelled",
    "subagent.completed",
    "subagent.spawned",
    "subagent.started",
    "token_counting.rebased",
    "turn.step.interrupted",
    # Two more from the same 2026-10-29 read of the audited sessions: tower
    # mode entry in 6a97dd66, per-step retries in bbad08a0.
    "tower_mode.enter",
    "turn.step.retrying",
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

# The full command is what a repeated-failure fingerprint hashes, so it is kept
# far longer than the display summary. Bounded all the same: a heredoc-pasted
# script can run to megabytes and the fact row must stay a fact row.
_COMMAND_MAX_CHARS = 4000
# Result output tail: long enough to hold a runner receipt or a pytest tail,
# short enough that 2,000 results stay readable.
_TAIL_MAX_CHARS = 2000
_EXCERPT_MAX_CHARS = 160

# A prompt whose whole text is a CLI-generated envelope is not the owner
# speaking: cron fires repeat verbatim by design, task notifications echo prior
# tool output, and tower wakes are inter-agent traffic. Same for the tower's
# wake line. T14 (repeated owner prompt) must not fire on the scheduler.
_SYSTEM_PROMPT_PREFIXES = ("<notification", "<cron-fire", "<task", "<prompt")
_SYSTEM_PROMPT_MARKERS = ("new tower inbox message",)


def _text_digest(text):
    """Normalized identity of a human sentence, for repeat detection.

    Case, punctuation and whitespace are noise in "why did you stop?" vs
    "Why did you stop?!" -- the same owner complaint. Digits are kept: two
    prompts that differ only by ticket number are about different tickets, and
    collapsing them would let one complaint about #675 masquerade as a repeat
    about #690.
    """
    words = re.findall(r"[a-z0-9]+", str(text).lower())
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()[:16]


def _is_system_prompt(text):
    body = str(text).lstrip()
    if body.startswith(_SYSTEM_PROMPT_PREFIXES):
        return True
    return any(marker in body for marker in _SYSTEM_PROMPT_MARKERS)


def _exit_code_from_output(output):
    """The exit code a shell reported in its own output, or None.

    The wire's tool.result carries no exit-code field (checked against all
    4,140 results in the three audited sessions: keys are exactly output,
    durationMs, note, isError). The bash tool appends
    ``Command failed with exit code: N``, which is a CLI-recorded value; when
    it is absent the code is genuinely unknown and stays null rather than
    guessed from ``isError``.
    """
    match = re.search(r"exit code:\s*(\d+)", str(output))
    return int(match.group(1)) if match else None


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
        # Additive (2026-10-29). Consumers of the older keys are unaffected.
        "assistant_texts": [],
        "owner_texts": [],
        "tool_results": [],
        "model_aliases": [],
        "turns": [],
        "tasks": [],
        "usage_records": 0,
    }
    # Insertion-ordered accumulators for the records that arrive in halves.
    calls = {}
    turns = {}
    tasks = {}

    def _turn_row(turn_id):
        key = str(turn_id)
        if key not in turns:
            turns[key] = {"turn": key, "started_at": None, "ended_at": None,
                          "duration_ms": None, "reason": None}
        return turns[key]

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
        if etype == "llm.request":
            facts["model_aliases"].append({
                "time": timestamp,
                "model": evt.get("model"),
                "alias": evt.get("modelAlias"),
            })
            continue
        if etype == "turn.prompt" or etype == "turn.steer":
            origin = evt.get("origin")
            if etype == "turn.steer" and isinstance(origin, dict) \
                    and origin.get("kind") not in (None, "user"):
                continue
            text = "\n".join(
                str(part.get("text", "")) for part in (evt.get("input") or [])
                if isinstance(part, dict) and part.get("type") == "text")
            if not text.strip():
                continue
            facts["owner_texts"].append({
                "time": timestamp,
                "turn": evt.get("turnId"),
                "source": etype,
                "system": _is_system_prompt(text),
                "digest": _text_digest(text),
                "chars": len(text),
                "excerpt": text[:_EXCERPT_MAX_CHARS],
            })
            continue
        if etype == "agent.turn.started":
            row = _turn_row(evt.get("turnId"))
            if row["started_at"] is None:
                row["started_at"] = timestamp
            continue
        if etype == "turn.ended":
            row = _turn_row(evt.get("turnId"))
            row["ended_at"] = timestamp
            row["duration_ms"] = evt.get("durationMs")
            row["reason"] = evt.get("reason")
            continue
        if etype == "task.started" or etype == "task.terminated":
            info = evt.get("info")
            if not isinstance(info, dict) or not info.get("taskId"):
                continue
            task_id = str(info.get("taskId"))
            row = tasks.setdefault(task_id, {
                "task_id": task_id, "started_at": None, "ended_at": None,
                "status": None, "exit_code": None, "detached": None,
                "description": None,
            })
            if etype == "task.started":
                row["started_at"] = info.get("startedAt") or timestamp
                row["detached"] = info.get("detached")
                row["description"] = info.get("description")
            else:
                row["ended_at"] = info.get("endedAt") or timestamp
            row["status"] = info.get("status") or row["status"]
            if info.get("exitCode") is not None:
                row["exit_code"] = info.get("exitCode")
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
            command = (str(args.get("command", ""))[:_COMMAND_MAX_CHARS]
                       if isinstance(args, dict) else "")
            row = {
                "time": timestamp,
                "turn": inner.get("turnId"),
                "step": inner.get("step"),
                "name": name,
                "summary": _summarize_args(name, args),
                "tool_call_id": inner.get("toolCallId"),
                "command": command,
            }
            facts["tool_calls"].append(row)
            calls[inner.get("toolCallId")] = (name, command)
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
        elif itype == "tool.result":
            result = inner.get("result")
            result = result if isinstance(result, dict) else {}
            output = str(result.get("output", ""))
            name, command = calls.get(inner.get("toolCallId"), (None, ""))
            facts["tool_results"].append({
                "time": timestamp,
                "turn": inner.get("turnId"),
                "tool_call_id": inner.get("toolCallId"),
                "name": name,
                "command": command,
                "exit_code": _exit_code_from_output(output),
                "is_error": bool(result.get("isError")),
                "tail": output[-_TAIL_MAX_CHARS:],
            })
        elif itype == "content.part":
            part = inner.get("part")
            if isinstance(part, dict) and part.get("type") == "text":
                text = str(part.get("text", ""))
                facts["assistant_text_lengths"].append({
                    "time": timestamp,
                    "turn": inner.get("turnId"),
                    "chars": len(text),
                })
                facts["assistant_texts"].append({
                    "time": timestamp,
                    "turn": inner.get("turnId"),
                    "digest": _text_digest(text),
                    "chars": len(text),
                    "excerpt": text[:_EXCERPT_MAX_CHARS],
                })

    facts["turns"] = list(turns.values())
    facts["tasks"] = list(tasks.values())
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
