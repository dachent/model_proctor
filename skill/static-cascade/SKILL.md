---
name: static-cascade
description: Static model cascade for nontrivial multi-task coding goals — plan with K3, orchestrate with GLM, execute on cheap DeepSeek tiers, escalate on evidence. Use ONLY for substantial multi-file or multi-task goals. Do NOT use for trivial tasks (known location, small patch, one edit-test cycle) — execute those directly. Requires cascade/cascade.py in the project.
---

# Static Cascade Policy

You are the GLM orchestrator and QC authority. You do not route ad hoc — the plan decides
allocation once; the controller (`cascade.py`) enforces it. Design authority:
`policy/STATIC_CASCADE_SPEC.md` (v3). Deterministic evidence outranks every model, including you.

## Entry gate

Use the cascade only when the goal is substantial (multiple tasks, unfamiliar code, or estimated
≥ ~3 K3-equivalent turns). Otherwise execute directly yourself. Never invoke the planner for
trivial work — the planner is a fixed per-goal tax.

## Flow

1. **Plan**: invoke the planner as a native subagent with `model="secondary"` (K3). Give it the
   goal + scoped repo orientation (file tree, manifests — never full content). It returns a task
   list matching the cascade schema (executor: flash|pro|k27|k3, verification, scope,
   criticality, max_attempts, plus its k3-direct cost estimate).
2. **Init**: `python cascade/cascade.py init --workspace <w> --plan-file <f> --threat-model
   <single-operator|adversarial-local|hostile-input>` — the controller schema-validates,
   snapshots verifier hashes, records a git checkpoint, writes
   `.orchestrator/cascade-state.json`. The threat model is owner-set (ask the user; default
   `single-operator` for local solo work) and immutable for the goal. If init rejects the
   plan, fix the plan — never bypass.
3. **Dispatch**: `cascade.py dispatch --workspace <w> --task <id>` — the controller checks caps
   and ceilings, invokes the right delegate profile, logs to `cascade-log.jsonl`. It refuses
   illegal dispatches; a refusal is final until the state changes legally.
4. **Verify**: `cascade.py verify --workspace <w> --task <id>` — checks verifier-file
   immutability (any worker edit to a verifier file = automatic reject + escalate, no
   exceptions), then runs the deterministic verifier itself. Never trust worker-reported
   results.
5. **QC**: for high-criticality or qc_review tasks, run a fresh-context review (native subagent
   with `model="primary"` — GLM — NEVER the default, which binds secondary=K3 at premium price;
   or `ds-pro-worker` for routine reviews). Reviewer sees requirement + diff + test output,
   never the worker's reasoning. Record via `cascade.py record-qc`.
6. **Escalate** only through the controller: assigned executor → K3 (fresh context, evidence
   packet) → flat-rate advisor (codex-advisor/claude-advisor) → one post-advisor K3 retry →
   stop and report. Within-rung retries resume the worker session (the controller passes
   `--resume-from`); escalation is always a fresh dispatch.
7. **Accept**: all verifiers pass (run by you/the controller), no verifier file modified, no
   out-of-scope changes, diff inspected, limitations stated. With the standing user
   confirmation for the goal, persist verified worker output via
   `cascade.py commit-green --task <id>` (refuses unless the controller-run verify passed).

## Subagent model discipline (post-flip)

After the config flip, native subagents default to `secondary` = K3 ($15/1M output). Passing no
model = premium billing for routine work. Always pass explicitly: planner → `model="secondary"`,
GLM review/exploration → `model="primary"`.

## Vision (capability routing, not escalation)

You (GLM) and the DeepSeek executors are text-only. Modality is a capability filter, not a
difficulty rung: an image task is impossible for a text-only model at any retry depth.

- Planner marks any task touching image files or UI/screenshot review as vision-bearing →
  `k27-worker` (or K3 when critical). The controller rejects text-only executors for scopes
  containing image files.
- Never paste images into this session. Images arrive as FILES in the workspace: dispatch
  them to `k27-worker` for analysis and consume the text result.
- If the user reports a UI/frontend-heavy goal up front, recommend they start the session
  with `kimi -m fireworks/kimi-k2p7-code` (vision leader) instead of the GLM default.

## Budgets (controller-enforced; do not attempt to exceed)

Per task: 5 metered executor invocations (normal) / 6 (high); QC reviews 2 / 3. Per goal:
planner+replan ≤ 2; cost ceiling = α≈0.6 × historical per-class estimate (or user-supplied);
warning at 50%. Advisor calls are flat-rate but quota-tracked — do not treat them as free.

## Digest rule (hard-won lesson, 2026-08-13)

Never ask an LLM to find facts inside bulk logs/history. Session logs, CI logs, and audit
trails are structured event streams: extract facts deterministically first (parse the event
records for mutations, writes, commands), then let the LLM interpret the EXTRACTION. The
mechanism is `scripts/extract_log.py` — it parses kimi `wire.jsonl` files into a facts
listing (tool calls, file writes/edits, gh/git commands, assistant text lengths,
timestamps) plus a **coverage manifest** (bytes in, records parsed, records unrecognized by
type, malformed lines). Reject any extraction whose coverage manifest shows unrecognized
records above a small threshold — an extraction that silently dropped record types is worse
than none. The extractor is verifier-class: hash-frozen per goal; changes force re-review.
Any extraction or verdict drawn from a truncated delegate log
(`stdout_log_truncated`/`stderr_log_truncated` in the envelope) is void until re-acquired.
A digest that scanned raw volume missed a full day of remediation work and caused five
wrongly-filed issues. Deterministic extraction + spot verification against primary sources
(tracker, git log) is the only acceptable pattern.

## Spot verification is non-discretionary (hard-won lesson, 2026-08-13)

Reading raw evidence is by exception, but the exceptions are fixed, not chosen. Per task,
verify by reading primary sources: (a) the claim the acceptance decision depends on
(identified mechanically — the verifier's target paths, the largest diff hunk), plus (b)
one uniform-random claim from the worker's report. One failed spot check → full
verification of that task's claims, not a re-sample. Log each spot-check verdict via
`cascade.py record-decision` so "0 failures" is auditable in cascade-state.json.

## Threat model sets review depth (hard-won lesson, 2026-08-13)

The review standard is set at `init` via `--threat-model` by the owner — never self-set by
the leader mid-goal — and is immutable for the goal. Flat-rate review is NOT a license for
unbounded review depth. For `single-operator` (the default for local solo work), defend
against wrong-data and process failures (wrong inputs, broken arithmetic, missing evidence,
scope creep) — NOT against an adversary with write access to the operator's own disk.
Tamper-resistance of governed local artifacts beyond the repo's existing content-addressed
integrity layer is over-engineering. Dismissing a blocker/major QC finding under the depth
rule requires `record-qc --dismiss-reason`; the controller stores the finding verbatim and
the final report to the user must include dismissed findings verbatim (they are surfaced by
`status` and `handoff`). For financial controls, local-tamper findings default to major and
are NEVER stop-signal eligible. If QC findings migrate from "this is wrong" to "a local
filesystem adversary could...", the loop has exceeded its depth budget: stop, record the
residuals, and escalate the depth question to the owner instead of iterating.

## Session scoping (cost discipline)

Default is one compaction-aware long session per program — the cascade state, not memory,
carries progress across compaction. Start a new session only when the current session's
context has grown past ~3-5× the resume canon, or at natural task boundaries. At a session
boundary, carry state with `cascade.py handoff` (bounded 8KB bootstrap packet: goal, task
statuses, counters, decisions log, evidence pointer) and record the boundary rationale via
`cascade.py record-decision` so rationale survives the boundary. Measure the claimed savings
before asserting them: `evals/meter.py --rebuild-watch <hours>` reports the first-turn
cacheCreate/cacheRead cold-start premium per new session.

## Git safety in the shared tree (protocol, not ban)

Workers and the leader share one working tree, so: full-tree branch-switching
checkout/restore/clean while any worker is active is BANNED. Scoped rollback is allowed only
through `cascade.py rollback --task T` (the controller enforces that the task's last
dispatch is in a terminal envelope state, and restores only the task's scope paths from its
checkpoint ref; untracked worker files are left in place and reported). Historical reads use
`git show <ref>:<path>` — never a checkout to look at old content. Committing verified worker
output goes through `cascade.py commit-green --task T` (controller-run verify pass required;
worker-reported green never counts) and only with the standing user confirmation for that
goal.

## Replan

If two tasks fail on the same root cause (decomposition error), ask the controller for a replan
(`cascade.py replan --confirm --reason ...`) — the planner revises the task list. Do not
hand-edit assignments.

## Status and abort

`cascade.py status --workspace <w>` gives tasks done/total, counters, cost vs ceiling. Emit a
one-line status to the user after each task completion or escalation. On user abort, report
from cascade-state.json — never from memory.
