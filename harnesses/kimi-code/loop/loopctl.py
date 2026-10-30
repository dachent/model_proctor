#!/usr/bin/env python3
"""loopctl — the deterministic half of the unified session controller loop.

The loop itself is one cron task whose prompt is rendered from
``template.md``. Everything in it that can be decided by a program instead of
by the model's good behaviour lives here, so the answer is the same on every
fire and the prompt only has to *report* it:

    bind              validate a spec, render the prompt, write installed.json,
                      print the exact CronCreate arguments
    verify            drift check (ACCIDENT DETECTION ONLY) against installed.json
    job0              run the wirewatch evaluator over this session's wires and
                      emit the single verdict line; exit 1 on stop-severity
    advancement       `advancement_allowed` as a CLI: may this fire spawn a worker?
    clear             owner-issued, counted clear of a delivered finding key
    notify            SessionHeartbeat hook entrypoint: toast + owner inbox
    status            read-only snapshot of the loop state dir

Fail-closed rules, and why they are here rather than in the prompt:

  - UNKNOWN/stale Job 0 suppresses advancement. Job 0 goes detached for large
    wire sets; without this rule the fire would read *last* fire's summary and
    spawn a worker while an escalation evaluation was still in flight.
  - unresolved stop-severity findings suppress advancement until the owner runs
    ``clear``. Nothing the worker writes can clear its own escalation.
  - the drift check catches a template edited after bind. It is labelled
    accident-detection everywhere because the live cron prompt cannot be read
    back (CronList truncates it) and both sides of the comparison are
    agent-writable.

The toast in ``notify`` is best-effort and untested on this host; the durable
guarantee is the on-disk record (``escalations.jsonl``, ``delivered.jsonl``,
``owner-inbox.jsonl``) plus re-delivery on the next fire.

TOOL-014: loop state lives outside every proctored workspace, and the wirewatch
output dir is validated by ``evaluator.check_out_dir`` on every Job 0.

Python 3.10, standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parents[2]
for _path in (_HERE, ROOT / "core", ROOT / "scripts",
              ROOT / "harnesses" / "kimi-code" / "wirewatch"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import evaluator  # noqa: E402
import loop_spec  # noqa: E402
import wire_triggers  # noqa: E402

# ── state dir layout ────────────────────────────────────────────────────
INSTALLED = "installed.json"
DELIVERED = "delivered.jsonl"
INBOX = "owner-inbox.jsonl"
LOCK = "lock.json"
JOB0_DIR = "job0"
JOB0_RUN = "job0/last_run.json"
WIREWATCH_DIR = "wirewatch"
ESCALATIONS = "wirewatch/escalations.jsonl"
SUMMARY = "wirewatch/summary.json"

DEFAULT_STATE_ROOT = Path("C:/Dev/bootstrap-state/model-proctor/loop")
DEFAULT_SESSIONS_ROOT = Path.home() / ".kimi-code" / "sessions"
NOTIFY_REPEAT_MINUTES = 30
JOB0_INLINE_SECONDS = 30
DEFAULT_JOB0_MAX_AGE_MS = 600_000

EXIT_BY_OUTCOME = {"NOMINAL": 0, "ESCALATED": 1, "UNKNOWN": 3, "LOCKED": 4,
                   "DRIFT": 5}
EXIT_USAGE = 2


# ── small deterministic helpers ─────────────────────────────────────────
def now_ms():
    return int(time.time() * 1000)


def finding_key(finding):
    """Identity key: (trigger, wire, evidence fingerprint).

    Deterministic and stable across runs — the same finding delivered twice
    hashes the same, which is what makes dedupe and owner-clear possible.
    """
    payload = json.dumps(
        {"trigger": finding.get("trigger"), "wire": finding.get("wire"),
         "evidence": finding.get("evidence") or {}},
        sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def read_jsonl(path):
    rows = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def append_jsonl(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def verdict_line(job, outcome, **fields):
    """One machine-parseable line, field order fixed by the caller.

    ``LOOP:v1 JOB0 ESCALATED stops=1 wires=6 key=abcd1234`` — the version header
    is the only version marker a transcript can be read for.
    """
    parts = [f"LOOP:v{loop_spec.TEMPLATE_VERSION}", job.upper(), outcome]
    parts += [f"{key}={value}" for key, value in fields.items()
              if value is not None]
    return " ".join(parts)


# ── installed state ─────────────────────────────────────────────────────
def installed_path(state_dir):
    return Path(state_dir) / INSTALLED


def load_installed(state_dir):
    return read_json(installed_path(state_dir), {}) or {}


def spec_for_state(state_dir, spec_path=None):
    """Rebuild the spec from a file, else from installed.json."""
    if spec_path:
        return loop_spec.load_spec(spec_path)
    installed = load_installed(state_dir)
    stored = installed.get("spec")
    if not stored:
        raise SystemExit(json.dumps(
            {"error": "no_installed_spec",
             "detail": "state dir has no installed.json with a spec; run "
                       "`loopctl bind --spec <file>` first",
             "state_dir": str(state_dir)}, sort_keys=True))
    return loop_spec.load_spec_dict(stored)


def spec_sha256(spec):
    blob = json.dumps(spec.to_json(), sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def template_sha256(path):
    text = Path(path).read_text("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── bind / verify ───────────────────────────────────────────────────────
def bind(spec, workspace=None, bound_at=None):
    """Validate, render, record. Never creates the cron: only the agent can."""
    problems = loop_spec.validate_spec(spec, workspace=workspace)
    if problems:
        raise SystemExit(json.dumps(
            {"error": "spec_invalid", "problems": problems,
             "session_id": spec.session_id}, indent=2, sort_keys=True))

    prompt = loop_spec.render_prompt(spec)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    state = Path(spec.state_dir).expanduser()
    state.mkdir(parents=True, exist_ok=True)

    installed = {
        "version": loop_spec.TEMPLATE_VERSION,
        "header": loop_spec.HEADER,
        "session_id": spec.session_id,
        "workspace": str(Path(workspace).resolve()) if workspace else None,
        "state_dir": str(state.resolve()),
        "cron_expr": spec.cron_expr,
        "cadence_minutes": spec.cadence_minutes,
        "fire_budget_s": spec.fire_budget_s,
        "job0_max_age_ms": spec.job0_max_age_ms,
        "prompt_sha256": digest,
        "template_sha256": template_sha256(spec.template_path),
        "spec_sha256": spec_sha256(spec),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "prompt_lines": prompt.count("\n"),
        "jobs": [job.as_dict() for job in spec.jobs],
        "outcomes": list(loop_spec.OUTCOMES),
        "drift_guard_scope": loop_spec.DRIFT_GUARD_SCOPE,
        "toast_delivery": loop_spec.TOAST_DELIVERY,
        "bound_at": bound_at if bound_at is not None else now_ms(),
        "spec": spec.to_json(),
    }
    write_json(installed_path(state), installed)
    return {
        "installed": str(installed_path(state).resolve()),
        "prompt_sha256": digest,
        "prompt_lines": installed["prompt_lines"],
        "prompt_bytes": installed["prompt_bytes"],
        "cron_arguments": loop_spec.cron_arguments(spec),
        "drift_guard_scope": loop_spec.DRIFT_GUARD_SCOPE,
        "next": "create exactly one cron with cron_arguments verbatim "
                "(delete the old slot in the same step when replacing)",
    }


def drift_check(state_dir, spec):
    """(outcome, detail) for the Job-4 accident check.

    ACCIDENT DETECTION ONLY. A pass means the template on disk still renders to
    the prompt recorded at bind. It does not mean the live cron prompt is that
    prompt: CronList truncates it, and installed.json is agent-writable.
    """
    installed = load_installed(state_dir)
    if not installed.get("prompt_sha256"):
        return "UNKNOWN", "never_bound"
    try:
        rendered = hashlib.sha256(
            loop_spec.render_prompt(spec).encode("utf-8")).hexdigest()
        template = template_sha256(spec.template_path)
    except (OSError, ValueError) as exc:
        return "UNKNOWN", f"render_failed:{type(exc).__name__}"
    if rendered != installed["prompt_sha256"]:
        return "DRIFT", "prompt_sha256_mismatch"
    if template != installed.get("template_sha256"):
        return "DRIFT", "template_sha256_mismatch"
    if installed.get("version") != loop_spec.TEMPLATE_VERSION:
        return "DRIFT", f"template_version:{installed.get('version')}"
    return "NOMINAL", "ok"


# ── Job 0 ───────────────────────────────────────────────────────────────
def resolve_wires(spec, sessions_root=None, session_id=None):
    root = Path(sessions_root or spec.kimi_sessions_root
                or DEFAULT_SESSIONS_ROOT).expanduser()
    session = session_id or spec.session_id
    patterns = [glob.replace("{session_id}", session)
                for glob in spec.wire_globs]
    found = set()
    for pattern in patterns:
        found.update(str(p) for p in root.glob(pattern) if Path(p).is_file())
    return sorted(found)


def acquire_lock(state_dir, ttl_ms, at=None):
    """(acquired, holder). Age-only, so the answer is the same on every host."""
    at = at if at is not None else now_ms()
    path = Path(state_dir) / LOCK
    holder = read_json(path, {})
    if holder:
        age = at - int(holder.get("acquired_at") or 0)
        if 0 <= age <= ttl_ms + 120_000:
            return False, holder
    write_json(path, {"pid": os.getpid(), "acquired_at": at, "ttl_ms": ttl_ms})
    return True, None


def release_lock(state_dir):
    path = Path(state_dir) / LOCK
    try:
        if read_json(path, {}).get("pid") == os.getpid():
            path.unlink()
    except OSError:
        pass


def record_delivery(state_dir, findings, at=None):
    """Mark escalation keys seen. Delivery/dedupe state, never a workspace write."""
    at = at if at is not None else now_ms()
    seen = {row.get("key") for row in read_jsonl(Path(state_dir) / DELIVERED)
            if row.get("kind") == "pending"}
    new = 0
    for finding in findings:
        if finding.get("severity") not in evaluator.ESCALATION_SEVERITIES:
            continue
        key = finding_key(finding)
        if key in seen:
            continue
        new += 1
        append_jsonl(Path(state_dir) / DELIVERED,
                     {"kind": "pending", "key": key, "first_seen": at,
                      "trigger": finding.get("trigger"),
                      "severity": finding.get("severity"),
                      "wire": finding.get("wire")})
    return new


def job0(state_dir, spec=None, wires=None, journal=None, cwd_boundary=None,
         at=None, session_id=None, sessions_root=None):
    """Evaluate this session's wires. Returns a result dict; never raises for
    a measurement outcome (a missing evaluator is a usage error, not UNKNOWN).
    """
    at = at if at is not None else now_ms()
    state = Path(state_dir).expanduser()
    spec = spec or spec_for_state(state)
    wire_paths = list(wires or [])
    if not wire_paths:
        wire_paths = resolve_wires(spec, sessions_root=sessions_root,
                                   session_id=session_id)
    if not wire_paths:
        return _job0_result(state, "UNKNOWN", "no_wires", at, {})

    total_bytes = sum(Path(p).stat().st_size for p in wire_paths
                      if Path(p).is_file())
    if (total_bytes > spec.job0_inline_max_bytes
            or len(wire_paths) > spec.job0_inline_max_wires):
        result = _job0_result(
            state, "UNKNOWN", "job0_detached_required", at,
            {"wires": len(wire_paths), "bytes": total_bytes})
        result["detach_command"] = _detach_command(state, spec, wire_paths,
                                                   journal)
        return result

    acquired, holder = acquire_lock(state, ttl_ms=2 * spec.fire_budget_s * 1000,
                                    at=at)
    if not acquired:
        return _job0_result(state, "LOCKED", "held", at,
                            {"holder_pid": holder.get("pid")})
    try:
        out = state / WIREWATCH_DIR
        try:
            evaluator.check_out_dir(out, wire_paths, cwd=cwd_boundary)
        except SystemExit as exc:
            return _job0_result(state, "UNKNOWN", "out_dir_refused", at,
                                {"detail": str(exc.code)})
        try:
            findings, per_wire = evaluator.evaluate_paths(wire_paths,
                                                          journal_path=journal)
            summary = evaluator.write_outputs(out, findings, per_wire,
                                             journal_path=journal)
        except Exception as exc:  # a broken measurement is named, never clear
            return _job0_result(state, "UNKNOWN",
                                f"evaluator_error:{type(exc).__name__}", at,
                                {"detail": str(exc)[:200]})
        stops = [finding_key(f) for f in findings
                 if f.get("severity") == "stop"]
        escalations = [f for f in findings
                       if f.get("severity") in evaluator.ESCALATION_SEVERITIES]
        new = record_delivery(state, escalations, at=at)
        outcome = "ESCALATED" if summary["stops"] else "NOMINAL"
        result = _job0_result(state, outcome, None, at, {
            "stops": summary["stops"],
            "lane_switch": summary["by_severity"].get("lane_switch", 0),
            "flag": summary["by_severity"].get("flag", 0),
            "wires": len(wire_paths),
            "unmeasurable": ",".join(sorted({u["trigger"] for u in
                                             summary["unmeasurable"]})) or "-",
            "new": new,
            "key": (stops[0] if stops else
                    (finding_key(escalations[0]) if escalations else "-")),
        })
        run = {
            "finished_at": at,
            "session_id": spec.session_id,
            "verdict": outcome,
            "reason": None,
            "stops": summary["stops"],
            "stop_keys": stops,
            "findings": summary["findings"],
            "wires": [str(Path(p).name) for p in wire_paths],
            "wire_bytes": total_bytes,
            "journal": str(journal) if journal else None,
            "summary_sha256": _sha256_file(out / "summary.json"),
            "job0_max_age_ms": spec.job0_max_age_ms,
        }
        write_json(state / JOB0_RUN, run)
        result["stop_keys"] = stops
        return result
    finally:
        release_lock(state)


def _job0_result(state, outcome, reason, at, fields):
    ordered = {}
    if reason:
        ordered["reason"] = reason
    for key in ("stops", "lane_switch", "flag", "wires", "unmeasurable",
                "new", "key"):
        if key in fields:
            ordered[key] = fields[key]
    line = verdict_line("JOB0", outcome, **ordered)
    result = {"outcome": outcome, "reason": reason, "line": line,
              "exit_code": EXIT_BY_OUTCOME[outcome], "state_dir": str(state),
              "at": at}
    result.update(fields)
    if outcome in ("UNKNOWN", "LOCKED"):
        write_json(Path(state) / JOB0_RUN, {
            "finished_at": at, "verdict": outcome, "reason": reason,
            "stops": 0, "stop_keys": [], "findings": 0})
    return result


def _detach_command(state, spec, wires, journal):
    parts = [sys.executable, str(Path(__file__).resolve()), "job0",
             "--state-dir", str(state)]
    for wire in wires:
        parts += ["--wire", str(wire)]
    if journal:
        parts += ["--journal", str(journal)]
    return parts


def _sha256_file(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


# ── advancement suppression ─────────────────────────────────────────────
def cleared_keys(state_dir):
    cleared = {}
    for row in read_jsonl(Path(state_dir) / DELIVERED):
        if row.get("kind") == "clear":
            cleared[row.get("key")] = row
    return cleared


def stop_keys(state_dir):
    """Stop-severity keys this fire's Job 0 produced, else the last output."""
    run = read_json(Path(state_dir) / JOB0_RUN, {}) or {}
    if "stop_keys" in run:
        return list(run["stop_keys"])
    keys = []
    for row in read_jsonl(Path(state_dir) / ESCALATIONS):
        if row.get("severity") == "stop":
            keys.append(row.get("key") or finding_key(row))
    return keys


def advancement_allowed(state_dir, at=None):
    """(allowed, reason). Fail closed: UNKNOWN is never clear.

    Suppressed when:
      - Job 0 never ran (no record to read);
      - Job 0's record is older than this fire's own run (detached, crashed, or
        the fire never got that far) — the disproof's stale-read fix;
      - Job 0's verdict is UNKNOWN / LOCKED / DRIFT;
      - any stop-severity finding is unresolved (no owner clear on record).
    """
    at = at if at is not None else now_ms()
    state = Path(state_dir)
    installed = load_installed(state)
    max_age = int(installed.get("job0_max_age_ms") or DEFAULT_JOB0_MAX_AGE_MS)
    run = read_json(state / JOB0_RUN, {}) or {}
    if not run:
        return False, "job0_never_ran"
    finished = run.get("finished_at")
    if not isinstance(finished, (int, float)):
        return False, "job0_unreadable:no_finished_at"
    age = at - finished
    if age > max_age:
        return False, f"job0_stale:age={age}ms>max={max_age}ms"
    verdict = run.get("verdict")
    if verdict in ("UNKNOWN", "LOCKED", "DRIFT"):
        return False, f"job0_{verdict.lower()}:{run.get('reason') or 'no_reason'}"
    cleared = cleared_keys(state)
    unresolved = sorted({k for k in stop_keys(state)
                         if k and k not in cleared})
    if unresolved:
        return False, ("stops_unresolved:%d:%s"
                       % (len(unresolved), ",".join(unresolved[:3])))
    resolved = len([k for k in stop_keys(state) if k in cleared])
    return True, ("ok:stops_cleared=%d" % resolved) if resolved else "ok"


def clear_key(state_dir, key, reason, at=None, by="owner"):
    """Counted, owner-issued clear. Refuses a key it has never seen."""
    at = at if at is not None else now_ms()
    state = Path(state_dir)
    known = {row.get("key") for row in read_jsonl(state / DELIVERED)
             if row.get("kind") == "pending"}
    known.update(stop_keys(state))
    if key not in known:
        return {"cleared": False,
                "detail": "key is not a delivered or pending finding; a clear "
                          "must name something that was actually delivered"}
    prior = [row for row in read_jsonl(state / DELIVERED)
             if row.get("kind") == "clear" and row.get("key") == key]
    record = {"kind": "clear", "key": key, "cleared_at": at, "by": by,
              "reason": reason, "clear_count": len(prior) + 1}
    append_jsonl(state / DELIVERED, record)
    return {"cleared": True, "record": record}


# ── notify: the SessionHeartbeat hook entrypoint ────────────────────────
def bindings(state_root=None):
    root = Path(state_root or os.environ.get("MODEL_PROCTOR_LOOP_ROOT")
                or DEFAULT_STATE_ROOT).expanduser()
    if not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        installed = read_json(child / INSTALLED)
        if isinstance(installed, dict) and installed.get("session_id"):
            out.append((child, installed))
    return out


def matching_state_dirs(payload, state_root=None, state_dir=None):
    if state_dir:
        return [Path(state_dir)]
    session = str(payload.get("session_id") or "")
    cwd = str(payload.get("cwd") or "")
    matched = []
    for path, installed in bindings(state_root):
        if session and installed.get("session_id") == session:
            matched.append(path)
            continue
        if not session and cwd and installed.get("workspace"):
            if Path(cwd).resolve() == Path(installed["workspace"]).resolve():
                matched.append(path)
    return matched


_TOAST_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$texts.Item(0).AppendChild($template.CreateTextNode($env:MP_TOAST_TITLE)) > $null
$texts.Item(1).AppendChild($template.CreateTextNode($env:MP_TOAST_BODY)) > $null
$toast = New-Object Windows.UI.Notifications.ToastNotification $template
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('model_proctor').Show($toast)
"""


def toast_windows(title, body, run=subprocess.run):
    """Best-effort Windows toast, then a msg.exe fallback.

    Zero-tested on this host and fail-open: the caller has already written the
    durable record. ``msg.exe`` is absent on Windows Home and needs a console
    session, so the fallback is a caveat, not a channel.
    """
    env = dict(os.environ, MP_TOAST_TITLE=title[:120], MP_TOAST_BODY=body[:400])
    try:
        run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
             _TOAST_SCRIPT], env=env, capture_output=True, text=True,
            timeout=15)
        return "toast"
    except Exception:
        pass
    try:
        run(["msg", "*", f"{title}: {body}"], capture_output=True, text=True,
            timeout=15)
        return "msg"
    except Exception:
        return "none"


def notify(payload, state_root=None, state_dir=None, toast=None,
           at=None, repeat_ms=None, dry_run=False):
    """Compare stops against delivered.jsonl; toast + inbox on a new one.

    Self-filtering is the first thing it does: a hook is host-global, so a
    payload from a session with no bound loop is a silent no-op.
    """
    at = at if at is not None else now_ms()
    repeat_ms = (repeat_ms if repeat_ms is not None
                 else NOTIFY_REPEAT_MINUTES * 60 * 1000)
    toast = toast or toast_windows
    dirs = matching_state_dirs(payload, state_root=state_root,
                               state_dir=state_dir)
    if not dirs:
        return {"action": "noop", "reason": "no_binding_for_session",
                "toasted": [], "silent": []}

    toasted, silent = [], []
    for state in dirs:
        state = Path(state)
        cleared = cleared_keys(state)
        last_toast = {}
        for row in read_jsonl(state / DELIVERED):
            if row.get("kind") == "toast" and row.get("key"):
                last_toast[row["key"]] = row.get("at") or 0
        keys = [k for k in dict.fromkeys(stop_keys(state))
                if k and k not in cleared]
        if not keys:
            silent.append({"state_dir": str(state), "reason": "no_uncleared_stop"})
            continue
        for key in keys:
            previous = last_toast.get(key)
            if previous is not None and at - int(previous) < repeat_ms:
                silent.append({"key": key, "reason": "recently_toasted"})
                continue
            title = "model_proctor: stop-severity escalation"
            body = (f"{key} in {state.name} is unresolved. "
                    f"Advancement is suppressed until the owner clears it.")
            channel = "dry-run" if dry_run else toast(title, body)
            append_jsonl(state / INBOX, {
                "kind": "stop", "key": key, "at": at, "channel": channel,
                "session_id": payload.get("session_id"),
                "cwd": payload.get("cwd"), "title": title, "body": body})
            append_jsonl(state / DELIVERED, {"kind": "toast", "key": key,
                                             "at": at, "channel": channel})
            toasted.append({"key": key, "channel": channel})
    action = "toasted" if toasted else "silent"
    return {"action": action, "toasted": toasted, "silent": silent,
            "toast_delivery": loop_spec.TOAST_DELIVERY}


# ── status ──────────────────────────────────────────────────────────────
def status(state_dir):
    state = Path(state_dir)
    installed = load_installed(state)
    run = read_json(state / JOB0_RUN, {}) or {}
    allowed, reason = advancement_allowed(state)
    return {
        "state_dir": str(state),
        "session_id": installed.get("session_id"),
        "bound_at": installed.get("bound_at"),
        "prompt_sha256": installed.get("prompt_sha256"),
        "job0": {"verdict": run.get("verdict"), "finished_at": run.get("finished_at"),
                 "stops": run.get("stops")},
        "advancement": {"allowed": allowed, "reason": reason},
        "unresolved_stops": [k for k in stop_keys(state)
                             if k not in cleared_keys(state)],
        "drift_guard_scope": loop_spec.DRIFT_GUARD_SCOPE,
        "toast_delivery": loop_spec.TOAST_DELIVERY,
    }


# ── CLI ────────────────────────────────────────────────────────────────
def _parser():
    parser = argparse.ArgumentParser(
        prog="loopctl",
        description="Deterministic half of the unified session controller loop.")
    sub = parser.add_subparsers(dest="command", required=True)

    bind_cmd = sub.add_parser("bind", help="validate a spec and render the prompt")
    bind_cmd.add_argument("--spec", required=True)
    bind_cmd.add_argument("--workspace", default=None,
                          help="proctored workspace; state_dir inside it is "
                               "refused (TOOL-014)")
    bind_cmd.add_argument("--print-prompt", action="store_true")

    job0_cmd = sub.add_parser("job0", help="run the wirewatch evaluator")
    job0_cmd.add_argument("--state-dir", required=True)
    job0_cmd.add_argument("--spec", default=None)
    job0_cmd.add_argument("--session", default=None)
    job0_cmd.add_argument("--sessions-root", default=None)
    job0_cmd.add_argument("--wire", action="append", default=None,
                          help="explicit wire path (repeatable); default is "
                               "the spec's wire globs")
    job0_cmd.add_argument("--journal", default=None)
    job0_cmd.add_argument("--cwd-boundary", default=None,
                          help="workspace boundary for the TOOL-014 check")
    job0_cmd.add_argument("--json", action="store_true")

    adv_cmd = sub.add_parser("advancement",
                             help="may this fire spawn a worker? (fail closed)")
    adv_cmd.add_argument("--state-dir", required=True)
    adv_cmd.add_argument("--json", action="store_true")

    verify_cmd = sub.add_parser("verify", help="drift check (accident detection)")
    verify_cmd.add_argument("--state-dir", required=True)
    verify_cmd.add_argument("--spec", default=None)

    clear_cmd = sub.add_parser("clear", help="owner-issued counted clear")
    clear_cmd.add_argument("--state-dir", required=True)
    clear_cmd.add_argument("--id", required=True, dest="key")
    clear_cmd.add_argument("--reason", required=True)

    notify_cmd = sub.add_parser("notify", help="SessionHeartbeat hook entrypoint")
    notify_cmd.add_argument("--state-root", default=None)
    notify_cmd.add_argument("--state-dir", default=None)
    notify_cmd.add_argument("--repeat-minutes", type=int,
                            default=NOTIFY_REPEAT_MINUTES)
    notify_cmd.add_argument("--no-toast", action="store_true")
    notify_cmd.add_argument("--dry-run", action="store_true")

    status_cmd = sub.add_parser("status", help="read-only state snapshot")
    status_cmd.add_argument("--state-dir", required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)

    if args.command == "bind":
        spec = loop_spec.spec_from_json_file(args.spec)
        result = bind(spec, workspace=args.workspace)
        print(json.dumps(result, indent=2, sort_keys=True))
        if args.print_prompt:
            print(loop_spec.render_prompt(spec))
        return 0

    if args.command == "job0":
        spec = loop_spec.load_spec(args.spec) if args.spec else None
        result = job0(args.state_dir, spec=spec, wires=args.wire,
                      journal=args.journal, cwd_boundary=args.cwd_boundary,
                      session_id=args.session, sessions_root=args.sessions_root)
        print(result["line"])
        if args.json:
            print(json.dumps({k: v for k, v in result.items() if k != "line"},
                             indent=2, sort_keys=True, default=str))
        return result["exit_code"]

    if args.command == "advancement":
        allowed, reason = advancement_allowed(args.state_dir)
        print(json.dumps({"allowed": allowed, "reason": reason},
                         sort_keys=True))
        return 0

    if args.command == "verify":
        spec = spec_for_state(args.state_dir, args.spec)
        outcome, detail = drift_check(args.state_dir, spec)
        print(verdict_line("JOB4", outcome, reason=detail,
                           scope="accident-detection-only",
                           key=(load_installed(args.state_dir)
                                .get("prompt_sha256") or "-")[:12]))
        return EXIT_BY_OUTCOME[outcome] if outcome in EXIT_BY_OUTCOME else 0

    if args.command == "clear":
        result = clear_key(args.state_dir, args.key, args.reason)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("cleared") else 1

    if args.command == "notify":
        raw = sys.stdin.read()
        if not raw.strip():
            print(json.dumps({"action": "noop", "reason": "empty_payload"}))
            return 0
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            print(json.dumps({"action": "noop", "reason": "unparseable_payload"}))
            return 0
        if not isinstance(payload, dict):
            print(json.dumps({"action": "noop", "reason": "unexpected_payload"}))
            return 0
        toast = None
        if args.no_toast:
            toast = lambda title, body: "suppressed"  # noqa: E731
        result = notify(payload, state_root=args.state_root,
                        state_dir=args.state_dir, toast=toast,
                        repeat_ms=args.repeat_minutes * 60 * 1000,
                        dry_run=args.dry_run)
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.command == "status":
        print(json.dumps(status(args.state_dir), indent=2, sort_keys=True))
        return 0

    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
