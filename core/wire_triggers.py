#!/usr/bin/env python3
"""Escalation triggers over recorded wire facts — pure, stdlib, no I/O.

Escalation redesign 2026-10-29 (audit: sessions 65f71599 / 6a97dd66 /
bbad08a0). The journal only sees runner-dispatched work; the tower and native
pods never touch it. The one surface that records everything is the session
``wire.jsonl``, parsed deterministically by ``scripts/extract_log.py``. This
module turns those facts into escalation findings.

Doctrine, binding:
  - no LLM consumes model output anywhere; findings prose is template-generated;
  - every trigger is a counter or pattern match over CLI-recorded facts, and no
    trigger reads a worker-authored value to decide NOT to fire;
  - evidence is deterministic — the same facts produce byte-identical findings;
  - a trigger whose required field is absent reports ``unmeasurable`` (named),
    never a silent pass.

Only the triggers achievable from the extractor's fields are implemented (the
disproof of the design, R3, forbids shipping the rest on hope):

  T2  nondiscriminating receipt at any dispatch count   -> stop
  T5  identical normalized (command, error-tail) run    -> lane_switch, then stop
  T6  edits-per-path in a window WITH a frozen failure  -> lane_switch
  T8  branch generations per ticket / same-branch rebase-> lane_switch
  T9  motion without progress on pending dispatched work-> lane_switch
  T10 measured wall vs journal-authorized budget (2x)   -> stop
  T14 same normalized owner prompt repeated             -> lane_switch

Not implemented here, and why: T1 needs a skill-load event the wire does not
carry (disproof R2); T3/T7/T11/T12/T13 and F* need fields or a same-intent
matcher this extractor does not produce (R3, R5). They are unmeasurable by
construction, and the evaluator says so rather than pretending they passed.

Thresholds are frozen here and bound by every harness; ``STAGNATION_THRESHOLD``
and ``DEFAULT_MAX_STAGNANT`` are *imported* from ``core/decisions.py`` — the
shared decision core — never re-derived (disproof, threshold corrections).

Python 3.10, standard library only.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:  # import as a plain module, like core/tests does
    sys.path.insert(0, _HERE)

import decisions  # noqa: E402

CONTRACT_VERSION = 1

SEVERITIES = ("lane_switch", "stop", "flag", "unmeasurable")


# ── frozen thresholds ───────────────────────────────────────────────────
@dataclass(frozen=True)
class Thresholds:
    """Every constant a trigger uses. Harnesses bind this; nothing re-derives.

    The stagnation pair is bound to ``core/decisions.py`` at class-definition
    time, so a change there moves every harness at once instead of leaving the
    wire evaluator counting a different run length than the runner.
    """
    # T5 — identical failure fingerprint. Design said 2->fire/3->stop; the
    # disproof showed that forks decisions.py (STAGNATION_THRESHOLD=3,
    # DEFAULT_MAX_STAGNANT=6) and violates HARNESS_CONTRACT. Bind instead.
    stagnation_threshold: int = decisions.STAGNATION_THRESHOLD
    max_stagnant: int = decisions.DEFAULT_MAX_STAGNANT
    # T6 — edits to one path inside a rolling window (design: >=5 / 10 min).
    edit_window_ms: int = 10 * 60 * 1000
    edits_per_path: int = 5
    # T8 — disproof R8: the audit's observed shape is the fire line, not the
    # flag line. >3 branches per ticket, >2 rebases of the same branch.
    branches_per_ticket_max: int = 3
    same_branch_rebase_max: int = 2
    # T9 — design: turn wall >30 min or summed sleep >10 min. Armed only by
    # pending dispatched work (disproof R4) and never on a bare turn.
    turn_wall_ms: int = 30 * 60 * 1000
    turn_sleep_s: int = 10 * 60
    min_tool_calls_per_turn: int = 3
    # T10 — audit: wall-clock/tokens >2x the ticket estimate -> escalate.
    budget_stop_ratio: float = 2.0
    # T14 — disproof R7: the loudest signal in the corpus.
    owner_prompt_repeat: int = 2
    # A result counts as a failure when the CLI flagged it, when it exited
    # non-zero, or when its own output carries a failure signature. The last
    # clause exists because the audited wrappers swallow exit codes (bb.ps1
    # printed "HTTP 404" from a call the wire reports as isError-absent).
    failure_markers: tuple = (
        "Traceback (most recent call last)",
        "Command failed with exit code",
        "SyntaxError",
        "HTTP 404",
        "HTTP 500",
        "No such file or directory",
        "AssertionError",
        "TypeError",
    )
    # A "commit" is the only progress that survives the session.
    commit_pattern: str = r"\bgit\s+(?:-C\s+\S+\s+)?commit\b"
    # Dispatch tools/commands that mean "work was handed to someone else".
    dispatch_tools: tuple = ("Agent", "AgentSwarm", "TowerSpawn")
    dispatch_pattern: str = r"(?:delegate\.py|runner\.py\s+dispatch)\b"


DEFAULTS = Thresholds()

# ── compiled patterns (derived from DEFAULTS' strings) ──────────────────
_RE_COMMIT = re.compile(DEFAULTS.commit_pattern)
_RE_DISPATCH = re.compile(DEFAULTS.dispatch_pattern)
_RE_BRANCH_CREATE = re.compile(
    r"git\s+(?:-C\s+\S+\s+)?(?:checkout\s+-b|switch\s+-c|worktree\s+add\s+-b)"
    r"\s+([^\s;&|]+)")
_RE_REBASE = re.compile(r"git\s+(?:-C\s+\S+\s+)?rebase\b")
_RE_TICKET = re.compile(r"\b([A-Za-z]{3,}[A-Za-z0-9]*-\d+)\b")
# Branch-operation words that look like ticket keys but name an action, not a
# tracker item. Without this, a branch called "merge-2" reports a ticket key.
_NOT_TICKETS = frozenset({"MERGE", "REBASE", "BUILD", "RELEASE", "HEAD", "PR"})
_RE_SLEEP = re.compile(
    r"(?:Start-Sleep\s+-Seconds\s+|Start-Sleep\s+-Milliseconds\s+|sleep\s+)"
    r"(\d+)")
_RE_SLEEP_UNIT_MS = re.compile(r"Start-Sleep\s+-Milliseconds\s+(\d+)")
_RE_RECEIPT_NONDISC = re.compile(r'"verifier_nondiscriminating"\s*:\s*true')
_RE_RECEIPT_PASSED_NULL = re.compile(r'"passed"\s*:\s*null')
_RE_RECEIPT_DISPATCH_SEQ = re.compile(r'"dispatch_seq"\s*:\s*\d+')


def _norm(text):
    """The core normalizer family, not a re-implementation of it."""
    return decisions.fingerprint(0, "", str(text or ""))


def _fingerprint(command, exit_code, tail):
    """(command, exit-tail) identity — both halves through the core normalizer."""
    return (_norm(command), decisions.fingerprint(exit_code or 0, "", tail))


def _tickets(text):
    """Ticket keys in a branch name or command, minus branch-operation words."""
    out = []
    for token in _RE_TICKET.findall(str(text or "")):
        prefix = token.rsplit("-", 1)[0].upper()
        if prefix in _NOT_TICKETS:
            continue
        out.append(token)
    return out


def _is_failure(result, cfg=DEFAULTS):
    if result.get("is_error"):
        return True
    code = result.get("exit_code")
    if code not in (None, 0):
        return True
    tail = str(result.get("tail") or "")
    return any(marker in tail for marker in cfg.failure_markers)


def _finding(trigger, severity, at, **evidence):
    return {"trigger": trigger, "severity": severity, "at": at,
            "evidence": evidence}


def _unmeasurable(trigger, reason, **evidence):
    return _finding(trigger, "unmeasurable", None, reason=reason, **evidence)


def _at(rows):
    return [r.get("time") for r in rows if isinstance(r.get("time"), (int, float))]


# ── T2: nondiscriminating receipt ───────────────────────────────────────
def _receipt_nondiscriminating(tail):
    """True when a tool result carries a runner receipt that cannot tell done
    from undone.

    Two shapes, both CLI-recorded:
      - ``"verifier_nondiscriminating": true`` (the record/accept output);
      - ``"passed": null`` on a receipt that names a dispatch_seq — a receipt
        taken before any verifier finished. ``last_receipt`` readouts that are
        merely empty carry ``"dispatch_seq": null`` and are NOT receipts, which
        is why the seq must be a number here.
    """
    text = str(tail or "")
    if _RE_RECEIPT_NONDISC.search(text):
        return "verifier_nondiscriminating"
    if _RE_RECEIPT_PASSED_NULL.search(text) and _RE_RECEIPT_DISPATCH_SEQ.search(text):
        return "passed_null"
    return None


def _t2(facts, cfg):
    results = facts.get("tool_results") or []
    if not results:
        return [_unmeasurable("T2", "wire has no tool.result records")]
    out = []
    for row in results:
        shape = _receipt_nondiscriminating(row.get("tail"))
        if shape is None:
            continue
        out.append(_finding(
            "T2", "stop", row.get("time"),
            receipt_shape=shape,
            tool_call_id=row.get("tool_call_id"),
            command=_excerpt(row.get("command")),
            tail=_excerpt(row.get("tail")),
        ))
    return out


def _excerpt(text, limit=200):
    return str(text or "")[:limit]


# ── T5: identical failure fingerprint ───────────────────────────────────
def _t5(facts, cfg):
    results = facts.get("tool_results") or []
    if not results:
        return [_unmeasurable("T5", "wire has no tool.result records")]
    run = {}
    out = []
    ordered = sorted(enumerate(results),
                     key=lambda pair: (pair[1].get("time") or 0, pair[0]))
    for _, row in ordered:
        if not _is_failure(row, cfg):
            # A clean run of the SAME command clears that command's run —
            # decisions.stagnation_run's "a pass clears the run" semantics,
            # scoped to the fingerprint's own command. An unrelated success
            # (a Read, a different script) is not evidence about this failure.
            command_fp = _norm(row.get("command"))
            for stale in [k for k in run if k[0] == command_fp]:
                run.pop(stale, None)
            continue
        key = _fingerprint(row.get("command"), row.get("exit_code"),
                           row.get("tail"))
        count = run.get(key, 0) + 1
        run[key] = count
        if count in (cfg.stagnation_threshold, cfg.max_stagnant):
            severity = ("stop" if count >= cfg.max_stagnant else "lane_switch")
            out.append(_finding(
                "T5", severity, row.get("time"),
                repeats=count,
                command=_excerpt(row.get("command")),
                command_fingerprint=key[0],
                failure_fingerprint=key[1],
                exit_code=row.get("exit_code"),
                is_error=bool(row.get("is_error")),
                tail=_excerpt(row.get("tail")),
            ))
    return out


# ── T6: edits-per-path inside a rolling window, with a frozen failure ───
def _t6(facts, cfg, t5_findings):
    if not (facts.get("file_mutations") or []):
        return []
    if not t5_findings:
        # Disproof R6: editing a test file while the verifier is red is the
        # defining step of TDD. Without a frozen failure fingerprint this is
        # legitimate repair, and firing here would criminalize it.
        return []
    failure_times = sorted(f.get("at") for f in t5_findings
                           if f.get("severity") in ("lane_switch", "stop")
                           and isinstance(f.get("at"), (int, float)))
    if not failure_times:
        return []
    by_path = {}
    for row in facts["file_mutations"]:
        if isinstance(row.get("time"), (int, float)):
            by_path.setdefault(row.get("path"), []).append(row["time"])
    out = []
    for path in sorted(by_path, key=lambda p: str(p)):
        times = sorted(by_path[path])
        fired = False
        for i, start in enumerate(times):
            window = [t for t in times[i:] if t - start <= cfg.edit_window_ms]
            if len(window) < cfg.edits_per_path or fired:
                continue
            frozen = [t for t in failure_times if t <= max(window)]
            if not frozen:
                continue
            fired = True
            out.append(_finding(
                "T6", "lane_switch", max(window),
                path=path,
                edits=len(window),
                window_ms=cfg.edit_window_ms,
                window_start=start,
                window_end=max(window),
                frozen_failure_trigger="T5",
                frozen_failure_at=max(frozen),
            ))
    return out


# ── T8: branch generations per ticket, and same-branch rebases ──────────
def _t8(facts, cfg):
    commands = []
    for row in facts.get("tool_calls") or []:
        command = str(row.get("command") or "")
        if command:
            commands.append((row.get("time"), command))
    if not commands:
        return [_unmeasurable("T8", "wire has no tool.call commands")]
    branches = {}
    rebases = {}
    unattributed_rebases = 0
    created = set()
    for time, command in commands:
        for match in _RE_BRANCH_CREATE.finditer(command):
            name = match.group(1).strip("\"'")
            created.add(name)
            for ticket in _tickets(name):
                branches.setdefault(ticket.upper(), {}).setdefault(name, time)
        if _RE_REBASE.search(command):
            names = set(re.findall(r"[A-Za-z0-9._/-]+", command))
            targets = {n for n in names if n in created}
            tickets = {t.upper() for t in _tickets(command)}
            if targets:
                for name in targets:
                    count, _ = rebases.get(name, (0, time))
                    rebases[name] = (count + 1, time)
            elif tickets:
                for ticket in tickets:
                    key = f"ticket:{ticket}"
                    count, _ = rebases.get(key, (0, time))
                    rebases[key] = (count + 1, time)
            else:
                unattributed_rebases += 1
    out = []
    for ticket in sorted(branches):
        names = branches[ticket]
        if len(names) > cfg.branches_per_ticket_max:
            latest = max(names.values())
            out.append(_finding(
                "T8", "lane_switch", latest,
                axis="branches_per_ticket", ticket=ticket,
                branches=len(names), max_allowed=cfg.branches_per_ticket_max,
                branch_names=sorted(names),
            ))
    for branch in sorted(rebases):
        count, last_time = rebases[branch]
        if count > cfg.same_branch_rebase_max:
            out.append(_finding(
                "T8", "lane_switch", last_time,
                axis="same_branch_rebase", branch=branch,
                rebases=count,
                max_allowed=cfg.same_branch_rebase_max,
                unattributed_rebases=unattributed_rebases,
            ))
    return out


# ── T9: motion without progress on pending dispatched work ──────────────
def _sleep_seconds(command):
    total = 0.0
    command = str(command or "")
    for match in _RE_SLEEP.finditer(command):
        value = int(match.group(1))
        if _RE_SLEEP_UNIT_MS.search(match.group(0)):
            total += value / 1000.0
        else:
            total += float(value)
    return total


def _t9(facts, cfg):
    turns = facts.get("turns") or []
    if not turns:
        return [_unmeasurable("T9", "wire has no turn records")]
    calls = facts.get("tool_calls") or []
    mutations = facts.get("file_mutations") or []
    mutation_times = sorted(t for t in _at(mutations))
    commit_times = sorted(t for t in
                          (row.get("time") for row in calls
                           if _RE_COMMIT.search(str(row.get("command") or "")))
                          if isinstance(t, (int, float)))
    progress_times = sorted(mutation_times + commit_times)
    dispatches = []
    for row in calls:
        name = row.get("name")
        command = str(row.get("command") or "")
        if name in cfg.dispatch_tools or _RE_DISPATCH.search(command):
            if isinstance(row.get("time"), (int, float)):
                dispatches.append((row["time"], name or "Bash",
                                   _excerpt(command or row.get("summary"),
                                            120)))
    out = []
    for turn in turns:
        turn_id = str(turn.get("turn"))
        ended = turn.get("ended_at")
        duration = turn.get("duration_ms")
        if ended is None and duration is None:
            continue
        turn_end = ended if isinstance(ended, (int, float)) else None
        start = turn.get("started_at")
        turn_calls = [row for row in calls if str(row.get("turn")) == turn_id
                      and isinstance(row.get("time"), (int, float))]
        sleeps = sum(_sleep_seconds(row.get("command")) for row in turn_calls)
        long_wall = isinstance(duration, (int, float)) and \
            duration >= cfg.turn_wall_ms
        long_sleep = sleeps >= cfg.turn_sleep_s
        if not (long_wall or long_sleep):
            continue
        # Disproof R4: never on a bare turn (interactive Q&A is legitimate).
        if len(turn_calls) < cfg.min_tool_calls_per_turn:
            continue
        horizon = turn_end if turn_end is not None else max(
            [row["time"] for row in turn_calls] or [0])
        pending = [d for d in dispatches
                   if (start is None or d[0] >= start) and d[0] <= horizon
                   and not any(d[0] < t <= horizon for t in progress_times)]
        if not pending:
            continue
        out.append(_finding(
            "T9", "lane_switch", horizon,
            turn=turn_id,
            wall_ms=duration,
            sleep_seconds=round(sleeps, 1),
            tool_calls=len(turn_calls),
            pending_dispatch_at=pending[0][0],
            pending_dispatch=_excerpt(pending[0][2], 120),
            reason="turn_wall" if long_wall else "summed_sleep",
        ))
    return out


# ── T10: measured wall vs the journal-authorized budget ─────────────────
def _t10(facts, cfg, journal):
    times = sorted(_at(facts.get("tool_calls") or []) +
                   _at(facts.get("tool_results") or []) +
                   [t for t in (row.get("started_at") for row in
                                (facts.get("turns") or []))
                    if isinstance(t, (int, float))] +
                   [t for t in (row.get("ended_at") for row in
                                (facts.get("turns") or []))
                    if isinstance(t, (int, float))])
    if not times:
        return [_unmeasurable("T10", "wire has no timestamps")]
    measured_s = (times[-1] - times[0]) / 1000.0
    budgets = [row.get("timeout_s") for row in (journal or [])
               if isinstance(row, dict) and row.get("event") == "dispatch_open"
               and isinstance(row.get("timeout_s"), (int, float))
               and row.get("timeout_s") > 0]
    if not budgets:
        # Disproof R5: lane records exist only in the runner journal, so the
        # old "no lane record" satisfier reduced to "big session on one model".
        # Without a journal-authorized estimate there is nothing to compare
        # against: named unmeasurable, never a pass.
        return [_unmeasurable(
            "T10", "no journal dispatch budget to compare the wall against",
            measured_seconds=round(measured_s, 1))]
    estimate_s = float(sum(budgets))
    ratio = measured_s / estimate_s if estimate_s else 0.0
    if ratio < cfg.budget_stop_ratio:
        return []
    return [_finding(
        "T10", "stop", times[-1],
        measured_seconds=round(measured_s, 1),
        estimate_seconds=round(estimate_s, 1),
        ratio=round(ratio, 3),
        dispatches_with_budget=len(budgets),
    )]


# ── T14: same normalized owner prompt repeated ──────────────────────────
def _t14(facts, cfg):
    owners = [row for row in (facts.get("owner_texts") or [])
              if not row.get("system")]
    if not owners:
        return [_unmeasurable("T14", "wire has no owner prompt records")]
    seen = {}
    out = []
    for row in sorted(owners, key=lambda r: r.get("time") or 0):
        digest = row.get("digest")
        count = seen.get(digest, 0) + 1
        seen[digest] = count
        if count == cfg.owner_prompt_repeat:
            out.append(_finding(
                "T14", "lane_switch", row.get("time"),
                repeats=count,
                digest=digest,
                source=row.get("source"),
                excerpt=row.get("excerpt"),
            ))
    return out


# ── entry point ─────────────────────────────────────────────────────────
CROSS_WIRE_TRIGGERS = ("T8",)


def evaluate(facts, journal=None, cfg=DEFAULTS):
    """Return findings for one extracted wire (plus an optional runner journal).

    ``facts`` is ``extract_log.extract_file``'s first return value. ``journal``
    is the parsed runner journal (list of records) or None. Findings are sorted
    by (time, trigger, evidence-JSON) so two runs over the same input are
    byte-identical.
    """
    facts = facts or {}
    t5 = _t5(facts, cfg)
    findings = []
    findings += _t2(facts, cfg)
    findings += t5
    findings += _t6(facts, cfg, t5)
    findings += _t8(facts, cfg)
    findings += _t9(facts, cfg)
    findings += _t10(facts, cfg, journal)
    findings += _t14(facts, cfg)
    return sorted(findings, key=lambda f: (
        f["at"] if isinstance(f["at"], (int, float)) else -1,
        f["trigger"],
        json.dumps(f["evidence"], sort_keys=True, default=str),
    ))


def evaluate_session(facts_list, journal=None, cfg=DEFAULTS):
    """Cross-wire triggers over a whole session (every agent's wire together).

    T8 counts branch generations per ticket and rebases of a single branch.
    Those are facts about the *repository*, not about one agent's wire, and the
    audited session split them across 53 subagent wires plus main — so
    evaluated per wire T8 is not merely quiet, it is unmeasurable in exactly
    the session it was built for. The evaluator unions the facts here and calls
    this once per invocation.
    """
    merged = {"tool_calls": [], "file_mutations": []}
    for facts in facts_list or []:
        merged["tool_calls"].extend(facts.get("tool_calls") or [])
        merged["file_mutations"].extend(facts.get("file_mutations") or [])
    merged["tool_calls"].sort(key=lambda r: r.get("time") or 0)
    return sorted(_t8(merged, cfg), key=lambda f: (
        f["at"] if isinstance(f["at"], (int, float)) else -1,
        f["trigger"],
        json.dumps(f["evidence"], sort_keys=True, default=str),
    ))


def load_journal(path):
    """Parse a runner journal.jsonl into records. Torn final lines are kept as
    ``journal_line_unparseable`` rather than dropped (the crash shape the
    journal exists to instrument)."""
    records = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                records.append({"event": "journal_line_unparseable",
                                "raw": line[-200:]})
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def summarize(findings):
    """Counts a reader can check without re-deriving the findings."""
    counts = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    return {
        "findings": len(findings),
        "by_severity": counts,
        "triggers": sorted({f["trigger"] for f in findings}),
        "unmeasurable": sorted({f["trigger"] for f in findings
                                if f["severity"] == "unmeasurable"}),
    }
