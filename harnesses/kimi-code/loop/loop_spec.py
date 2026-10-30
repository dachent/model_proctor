#!/usr/bin/env python3
"""The unified session controller loop's spec, and its prompt renderer.

One cron per session, one prompt, five jobs in a fixed order, every fire
bounded. This module owns the *shape* of that loop: the job registry, the
budgets, the state locations, and the byte-stable rendering of
``template.md`` into the prompt the cron re-injects on every fire.

Design record: ``review/loop-design.md``; the binding corrections are from
``review/disprove-loop.md`` and are folded in here and in the template:

  - a fire is *bounded-blocking*, never "non-blocking": steering the owner
    types queues behind at most one fire budget;
  - Job 0's UNKNOWN/stale output suppresses advancement (invariant 13 is keyed
    on "did this fire's Job 0 land", not only on positive stops);
  - the drift guard is accident detection, never a proof;
  - roughly half the invariants are conventions, not enforced rules, and the
    prompt says which is which;
  - advancement suppression is enforced by a host ``PreToolUse`` hook
    (``harnesses/kimi-code/hooks/proctor_advancement_gate.ps1``), not by the
    prompt asking nicely.

Thresholds are *not* a per-session choice: this module binds
``core/wire_triggers.DEFAULTS``, which binds ``core/decisions.py``. A session
supplies its own cadence, state dir, wires and ledger — never its own
thresholds (``policy/HARNESS_CONTRACT.md``).

Python 3.10, standard library only.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parents[2]
for _path in (ROOT / "core", ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import decisions  # noqa: E402
import wire_triggers  # noqa: E402

TEMPLATE_VERSION = 1
HEADER = f"LOOP:v{TEMPLATE_VERSION}"

# The fixed order. Job 0 runs first because a stop that is visible later in the
# fire still must suppress the spawn at the end of it.
JOB_ORDER = ("escalate", "delta", "verdict", "advance", "reconcile")

# The entire outcome vocabulary. Every branch of every job ends in one of these
# five strings — string drift between prompt and emitter is the defect class
# that produced bbad08a0's two silent no-ops.
OUTCOMES = ("ESCALATED", "NOMINAL", "UNKNOWN", "LOCKED", "DRIFT")

# The drift guard's scope, stated where the code reads it and carried into
# installed.json so no reader has to infer it from the design prose.
DRIFT_GUARD_SCOPE = (
    "accident-detection-only: re-render vs installed.json catches a template "
    "edited after bind; it cannot prove the live cron prompt is the rendered "
    "one, because both sides are agent-writable and CronList truncates the "
    "stored prompt"
)

# The toast is the least-tested layer and is labelled as such everywhere it
# ships. The durable guarantee is the on-disk record plus re-delivery.
TOAST_DELIVERY = (
    "best-effort, zero-tested on this host; the durable guarantee is the "
    "on-disk record plus re-delivery on the next fire"
)

TEMPLATE_PATH = _HERE / "template.md"


@dataclass(frozen=True)
class JobSpec:
    """One job in the fixed order: its budget and the strings it may emit."""
    key: str
    index: int
    title: str
    budget_s: int
    outcomes: tuple

    def as_dict(self):
        return {"index": self.index, "key": self.key, "title": self.title,
                "budget_s": self.budget_s, "outcomes": list(self.outcomes)}


def default_jobs():
    """The v1 registry. Budgets sum to 105 s against a 180 s fire."""
    return (
        JobSpec("escalate", 0, "ESCALATE", 30,
                ("ESCALATED", "NOMINAL", "UNKNOWN", "LOCKED")),
        JobSpec("delta", 1, "DELTA", 30, ("NOMINAL", "UNKNOWN")),
        JobSpec("verdict", 2, "VERDICT + DELIVER", 15, ("NOMINAL", "UNKNOWN")),
        JobSpec("advance", 3, "ADVANCE", 15, ("NOMINAL", "UNKNOWN")),
        JobSpec("reconcile", 4, "RECONCILE", 15, ("NOMINAL", "DRIFT")),
    )


@dataclass(frozen=True)
class LoopSpec:
    """Everything a session owns about its loop. Nothing else is per-session."""
    session_id: str
    state_dir: str
    cadence_minutes: int = 10
    fire_budget_s: int = 180
    jobs: tuple = field(default_factory=default_jobs)
    kimi_sessions_root: str = ""
    wire_globs: tuple = ("*/{session_id}/agents/*/wire.jsonl",)
    journal_path: str = ""
    max_output_lines: int = 5
    job0_max_age_ms: int = 600_000
    job0_inline_max_bytes: int = 5_000_000
    job0_inline_max_wires: int = 8
    ledger: tuple = ()
    template_version: int = TEMPLATE_VERSION
    template_path: str = str(TEMPLATE_PATH)
    thresholds: wire_triggers.Thresholds = wire_triggers.DEFAULTS

    # ── derived ─────────────────────────────────────────────────────────
    @property
    def cadence_ms(self):
        return self.cadence_minutes * 60 * 1000

    @property
    def cron_expr(self):
        if self.cadence_minutes < 1 or self.cadence_minutes > 59:
            raise ValueError("cadence_minutes must be 1..59 for a cron minute "
                             f"step; got {self.cadence_minutes}")
        return f"*/{self.cadence_minutes} * * * *"

    @property
    def total_job_budget_s(self):
        return sum(job.budget_s for job in self.jobs)

    def expanded_wire_globs(self):
        return tuple(glob.replace("{session_id}", self.session_id)
                     for glob in self.wire_globs)

    def to_json(self):
        """The ``load_spec``-compatible form (no derived fields, no thresholds).

        ``installed.json`` stores this so ``loopctl verify`` can re-render the
        recorded spec without the spec file still being where it was bound.
        """
        return {
            "session_id": self.session_id,
            "state_dir": self.state_dir,
            "cadence_minutes": self.cadence_minutes,
            "fire_budget_s": self.fire_budget_s,
            "kimi_sessions_root": self.kimi_sessions_root,
            "wire_globs": list(self.wire_globs),
            "journal_path": self.journal_path,
            "max_output_lines": self.max_output_lines,
            "job0_max_age_ms": self.job0_max_age_ms,
            "job0_inline_max_bytes": self.job0_inline_max_bytes,
            "job0_inline_max_wires": self.job0_inline_max_wires,
            "ledger": list(self.ledger),
            "template_version": self.template_version,
            "template_path": self.template_path,
        }

    def as_dict(self):
        return {
            "template_version": self.template_version,
            "session_id": self.session_id,
            "state_dir": self.state_dir,
            "cadence_minutes": self.cadence_minutes,
            "cron_expr": self.cron_expr,
            "fire_budget_s": self.fire_budget_s,
            "total_job_budget_s": self.total_job_budget_s,
            "jobs": [job.as_dict() for job in self.jobs],
            "kimi_sessions_root": self.kimi_sessions_root,
            "wire_globs": list(self.expanded_wire_globs()),
            "journal_path": self.journal_path,
            "max_output_lines": self.max_output_lines,
            "job0_max_age_ms": self.job0_max_age_ms,
            "job0_inline_max_bytes": self.job0_inline_max_bytes,
            "job0_inline_max_wires": self.job0_inline_max_wires,
            "ledger": list(self.ledger),
            "thresholds": {k: (list(v) if isinstance(v, tuple) else v)
                           for k, v in _threshold_fields(self.thresholds)},
            "drift_guard_scope": DRIFT_GUARD_SCOPE,
            "toast_delivery": TOAST_DELIVERY,
        }


def _threshold_fields(cfg):
    return [(name, getattr(cfg, name))
            for name in sorted(vars(cfg)) if name != "failure_markers"] + \
           [("failure_markers", cfg.failure_markers)]


# ── spec loading + validation ───────────────────────────────────────────
_SPEC_KEYS = {
    "session_id", "state_dir", "cadence_minutes", "fire_budget_s",
    "kimi_sessions_root", "wire_globs", "journal_path", "max_output_lines",
    "job0_max_age_ms", "job0_inline_max_bytes", "job0_inline_max_wires",
    "ledger", "template_version", "template_path",
}


def load_spec_dict(data):
    """Rebuild a spec from ``LoopSpec.to_json()`` (the installed.json form)."""
    if not isinstance(data, dict):
        raise ValueError("spec must be a JSON object")
    unknown = set(data) - _SPEC_KEYS
    if unknown:
        raise ValueError(f"stored spec carries keys this contract does not "
                         f"own: {sorted(unknown)}")
    kwargs = dict(data)
    for key in ("wire_globs", "ledger"):
        if key in kwargs:
            kwargs[key] = tuple(kwargs[key])
    return LoopSpec(**kwargs)


def load_spec(path):
    """Load a session spec from JSON.

    Unknown keys are an error, not an extension point: a typo in a spec is a
    silent default, and a silent default in a controller is a silent no-op.
    Thresholds may not appear at all — they bind the core (HARNESS_CONTRACT).
    """
    data = json.loads(Path(path).read_text("utf-8"))
    return load_spec_dict(data)


def validate_spec(spec, workspace=None):
    """Return a list of violations. Empty means the spec may be bound."""
    problems = []
    if not str(spec.session_id or "").strip():
        problems.append("session_id is empty")
    if spec.template_version != TEMPLATE_VERSION:
        problems.append(f"template_version {spec.template_version} != "
                        f"{TEMPLATE_VERSION}")
    keys = tuple(job.key for job in spec.jobs)
    if keys != JOB_ORDER:
        problems.append(f"job order must be {JOB_ORDER}; got {keys}")
    for job in spec.jobs:
        if job.budget_s <= 0:
            problems.append(f"job {job.key} has a non-positive budget")
        stray = set(job.outcomes) - set(OUTCOMES)
        if stray:
            problems.append(f"job {job.key} emits unknown outcome strings "
                            f"{sorted(stray)}")
    if spec.total_job_budget_s > spec.fire_budget_s:
        problems.append(
            f"job budgets sum to {spec.total_job_budget_s}s > fire budget "
            f"{spec.fire_budget_s}s")
    # Invariant 3: cadence must exceed the unit of work by 3x, or fires queue.
    if spec.cadence_minutes * 60 < 3 * spec.fire_budget_s:
        problems.append(
            f"cadence {spec.cadence_minutes}min does not exceed 3x the "
            f"{spec.fire_budget_s}s fire budget")
    if not spec.wire_globs:
        problems.append("wire_globs is empty: Job 0 would have nothing to read")
    for glob in spec.expanded_wire_globs():
        if not glob.endswith("wire.jsonl"):
            problems.append(f"wire glob does not name a wire: {glob}")
    if not 1 <= spec.max_output_lines <= 12:
        problems.append("max_output_lines must be 1..12; the prompt is "
                        "re-injected every fire")
    if spec.job0_max_age_ms <= 0 or spec.job0_max_age_ms > spec.cadence_ms:
        problems.append(
            f"job0_max_age_ms {spec.job0_max_age_ms} must be positive and "
            f"within one cadence ({spec.cadence_ms}ms): a Job-0 record older "
            "than this fire's own run is stale, and stale is not clear")
    state = str(spec.state_dir or "").strip()
    if not state:
        problems.append("state_dir is empty")
    else:
        target = Path(state).expanduser()
        if not target.is_absolute():
            problems.append("state_dir must be absolute; loop state lives "
                            "outside every proctored workspace (TOOL-014)")
        elif workspace:
            try:
                inside = Path(workspace).resolve() in target.resolve().parents \
                    or target.resolve() == Path(workspace).resolve()
            except OSError:
                inside = False
            if inside:
                problems.append(
                    f"state_dir {target} is inside the workspace {workspace}: "
                    "a worker that can rewrite loop state can suppress its own "
                    "escalation (TOOL-014)")
    # Bind, never fork: the threshold pair must still be the core's.
    if spec.thresholds.stagnation_threshold != decisions.STAGNATION_THRESHOLD:
        problems.append("thresholds.stagnation_threshold has forked "
                        "core/decisions.STAGNATION_THRESHOLD")
    if spec.thresholds.max_stagnant != decisions.DEFAULT_MAX_STAGNANT:
        problems.append("thresholds.max_stagnant has forked "
                        "core/decisions.DEFAULT_MAX_STAGNANT")
    return problems


def with_template(spec, path):
    """A copy pointed at another template — the drift test's only tool."""
    return replace(spec, template_path=str(path))


# ── prompt rendering ────────────────────────────────────────────────────
_SLOT = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def _thresholds_block(cfg):
    minutes = lambda ms: f"{ms // 60000}min"  # noqa: E731
    rows = [
        f"stagnation_threshold={cfg.stagnation_threshold}",
        f"max_stagnant={cfg.max_stagnant}",
        f"edits_per_path={cfg.edits_per_path}",
        f"edit_window={minutes(cfg.edit_window_ms)}",
        f"branches_per_ticket_max={cfg.branches_per_ticket_max}",
        f"same_branch_rebase_max={cfg.same_branch_rebase_max}",
        f"turn_wall={minutes(cfg.turn_wall_ms)}",
        f"turn_sleep={cfg.turn_sleep_s}s",
        f"budget_stop_ratio={cfg.budget_stop_ratio}",
        f"owner_prompt_repeat={cfg.owner_prompt_repeat}",
    ]
    return ", ".join(rows)


def _jobs_block(spec):
    lines = []
    for job in spec.jobs:
        lines.append(f"  JOB {job.index} {job.title} - budget {job.budget_s}s; "
                     f"outcomes: {' | '.join(job.outcomes)}")
    return "\n".join(lines)


def _ledger_block(spec):
    if not spec.ledger:
        return "  (empty - no ledger items are outstanding)"
    return "\n".join(f"  {i + 1}. {item}" for i, item in enumerate(spec.ledger))


def render_prompt(spec):
    """Render ``template.md`` for this session. Byte-stable.

    Two renders of the same spec produce identical bytes on any platform:
    line endings are normalized to LF before and after substitution, no
    timestamp or host fact is substituted, and dict ordering never reaches the
    output. That is what makes the sha256 in ``installed.json`` meaningful for
    accident detection.
    """
    template_path = Path(spec.template_path)
    text = template_path.read_text("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    values = {
        "LOOP_VERSION": str(spec.template_version),
        "HEADER": HEADER,
        "SESSION_ID": spec.session_id,
        "CADENCE": str(spec.cadence_minutes),
        "CRON": spec.cron_expr,
        "FIRE_BUDGET": str(spec.fire_budget_s),
        "TOTAL_JOB_BUDGET": str(spec.total_job_budget_s),
        "STATE_DIR": str(spec.state_dir),
        "SESSIONS_ROOT": spec.kimi_sessions_root or "<kimi sessions root>",
        "WIRE_GLOBS": ", ".join(spec.expanded_wire_globs()),
        "JOURNAL": spec.journal_path or "<none>",
        "MAX_LINES": str(spec.max_output_lines),
        "JOB0_MAX_AGE": f"{spec.job0_max_age_ms // 1000}s",
        "JOB0_MAX_BYTES": f"{spec.job0_inline_max_bytes // 1_000_000} MB",
        "JOB0_MAX_WIRES": str(spec.job0_inline_max_wires),
        "JOBS": _jobs_block(spec),
        "OUTCOMES": " | ".join(OUTCOMES),
        "THRESHOLDS": _thresholds_block(spec.thresholds),
        "LEDGER": _ledger_block(spec),
    }
    missing = sorted({m.group(1) for m in _SLOT.finditer(text)} - set(values))
    if missing:
        raise ValueError(f"template uses slots this renderer does not supply: "
                         f"{missing}")
    rendered = _SLOT.sub(lambda m: values[m.group(1)], text)

    # The header is the only version marker a transcript can be read for, so
    # it is asserted rather than trusted to the template body.
    first = rendered.split("\n", 1)[0]
    if not first.startswith(f"{HEADER} "):
        rendered = f"{HEADER} unified session controller loop\n" + rendered
    return rendered if rendered.endswith("\n") else rendered + "\n"


def prompt_sha256(spec):
    import hashlib
    return hashlib.sha256(render_prompt(spec).encode("utf-8")).hexdigest()


def cron_arguments(spec):
    """The exact CronCreate arguments. Only the agent can create the slot."""
    return {"cron": spec.cron_expr, "prompt": render_prompt(spec),
            "recurring": True}


def spec_from_json_file(path):
    """Convenience for the CLI: load, then rebind the template if it moved."""
    spec = load_spec(path)
    if not Path(spec.template_path).is_file():
        spec = with_template(spec, TEMPLATE_PATH)
    return spec


if __name__ == "__main__":  # tiny self-check: render the default spec
    demo = LoopSpec(session_id=os.environ.get("LOOP_SESSION_ID", "session-demo"),
                    state_dir=os.environ.get(
                        "LOOP_STATE_DIR",
                        "C:/Dev/bootstrap-state/model-proctor/loop/demo"))
    print(render_prompt(demo))
