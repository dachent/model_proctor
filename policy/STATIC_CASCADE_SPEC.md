# Static Model Cascade Spec (v3)

**Status:** v3 — adjudicated after two expert advisor reviews (Codex + Claude) of v2; 15
findings accepted and incorporated, 2 rejected/deferred (§11).
**Supersedes:** the dynamic per-turn routing policy and this spec's v1/v2. The dynamic
design's pre-registered magnitude predictions (≥2×/≥3× cost reduction) did not materialize.
The scorecard's cost metric was a broken proxy (`tokens_reported: null` throughout; all 18
showcase runs passed acceptance and hidden tests at 1.00 — fixtures never discriminated on
quality). Retroactive real metering (`evals/results-metered.jsonl`) shows C at +16% and D at
+37% vs A on real cost (config medians across all showcase runs). The static design rests on
**pre-generation-routing evidence** (§2), not on a falsification claim — the prior result is
uninterpretable on cost, not falsified.
**Objective:** minimize metered (Fireworks) cost per completed coding task at fixed quality.
Latency is not a hard constraint but is a secondary metric (soft guardrail: cascade wall-clock
≤ 2× baseline; abandoned sessions are 100% waste — failed/aborted goals count in the cost
denominator, §7). Codex/Claude CLI advisors are flat-rate subscription (quota-bound, not
free-unlimited) and sit at the apex.

## 0. Cost model

The cascade's cost sign depends on cache state, which the delegate wrapper's stateless calls
forfeit. This section makes the arithmetic explicit so §11's break-even conditions are
**model-derived estimates** (marked as such), not assertions.

**Closed-form per-role cost:**

```
cost = Σ_role (uncached_in · p_in + cached_in · p_cached + out · p_out)
```

where `p_in`, `p_cached`, `p_out` are the role's per-1M-token prices from §3 / `evals/pricing.yaml`,
and `uncached_in`, `cached_in`, `out` are the token counts for that role's invocation(s). The
sum runs over ALL roles: planner, orchestrator (GLM session turns), executors, QC reviewers,
retries, and cache-creation tokens. Failed/aborted runs are included (§7 total-cost definition).

**Worked comparison** (representative single turn, 60K input / 3K output; cache rates from
`evals/pricing.yaml`):

| Path | Input $ | Output $ | Turn $ |
|---|---|---|---|
| K3 warm session (80% cache hit) | 0.0504 | 0.0450 | **0.095** |
| DS-Pro via stateless delegate (0% cache) | 0.1044 | 0.0104 | **0.115** |
| K3 cold (0% cache) | 0.1800 | 0.0450 | **0.225** |
| DS-Flash via stateless delegate | 0.0084 | 0.0008 | **0.009** |

Arithmetic: K3 warm (80% cache) = 60K × 0.20 × $3/1M + 60K × 0.80 × $0.30/1M + 3K × $15/1M =
$0.036 + $0.0144 + $0.045 = $0.095. DS-Pro stateless = 60K × $1.74/1M + 3K × $3.48/1M =
$0.1044 + $0.0104 = $0.115. K3 cold = 60K × $3/1M + 3K × $15/1M = $0.18 + $0.045 = $0.225.
DS-Flash = 60K × $0.14/1M + 3K × $0.28/1M = $0.0084 + $0.0008 = $0.009.

**Decisive implication:** stateless delegate calls convert cached input tokens into uncached
input tokens. Agentic coding is input-dominated. DS-Pro-as-default-executor **only beats a
COLD K3 session** (~2×); against a warm K3 session it is **more expensive** ($0.115 vs $0.095).
**Flash wins regardless** (~10× vs warm K3, ~25× vs cold K3). This is why v3 inverts the
default executor to Flash (§3, §4). The break-even conditions in §11 are derived from this
model under assumed cache-hit rates — they are **model-derived estimates**, not measured
constants.

## 1. Design constraints

1. All Fireworks models are metered per token; Codex/Claude CLI calls are flat-rate
   (subscription-quota-bound, not free-unlimited).
2. Frontier subscription models are used only as bounded, read-only advisors. Their weekly
   subscription quota is tracked in the cascade log (§7).
3. No dynamic per-turn routing. Allocation is decided once at plan time by the planner and is
   **controller-enforced** (§4, §12 component `cascade.py`). The controller owns
   cascade-state.json transitions, JSON-schema-validates the planner's task list, enforces
   caps/counters and legal escalation transitions, and is the only path that invokes delegate
   in cascade mode. GLM decides classification/QC **within** legal transitions; the controller
   rejects illegal ones. GLM is a decision component, not the control plane.
4. Escalation is evidence-driven only: deterministic verification (tests/compilers/linters)
   first, QC judgment second. No learned scorers, no confidence vibes.
5. The session leader is GLM-5.2, not K3 (cost: $4.40 vs $15.00 per 1M output).
6. **Cascade-entry gate:** trivial/small tasks (known location, small patch, one edit-test
   cycle, or anything estimated under ~3 K3-equivalent turns) never enter the cascade — the
   GLM orchestrator does them directly. The K3 planner is only invoked for multi-task or
   substantial goals. (The planner is a fixed per-goal tax; on small tasks it exceeds the
   work it saves — measured in the pilot. Config E, §10.4, tests whether a flat-rate advisor
   planner eliminates this tax.)

## 2. Evidence base

### Established (primary sources, single-turn unless noted)
- Pre-generation (plan-time) routing beats post-generation cascading on 4 of 5 benchmarks,
  because cascades pay the cheap model's structural cost on every escalation. Exception:
  TriviaQA, where the pre-generation signal was uninformative and post-generation confidence
  was the only viable signal. Benchmarks include LiveCodeBench (code) but are ALL single-turn;
  multi-turn agentic transfer is untested. (arXiv:2605.06350)
- 2-tier difficulty cascades are optimal; extra mandatory tiers add cost without held-out
  gains. Applies to difficulty chains on the SAME task — see "Partially established" for how
  it bears on this design. (arXiv:2605.06350)
- Cascading beats baseline only when the verifier/judge error rate ≤ 0.1; at 0.2 performance
  may deteriorate rapidly. Single-turn, includes MBPP but not agentic coding.
  (RouterBench, arXiv:2403.12031)
- Deterministic verification (code execution) as the escalation signal works in an agentic
  setting: EcoAssistant, +10pts success at <50% cost. Note: EcoAssistant's escalation is
  DYNAMIC; this spec borrows its verifier mechanism but replaces dynamic routing with static
  plan-time allocation. (arXiv:2310.03046)

### Partially established / cautious
- Static phase pipelines with test-based validation achieve low cost-per-resolved-issue in
  SINGLE-model configuration (Agentless, arXiv:2407.01489 — $0.34–0.70/issue, SWE-bench Lite).
  Whether multi-model role assignment within such a pipeline preserves the cost advantage is
  untested — Agentless uses one model throughout.
- TwinRouterBench (arXiv:2605.18859, May 2026) is the closest published benchmark: dynamic
  per-step routing in multi-turn agentic coding on SWE-bench Verified. Static plan-time
  allocation remains untested even there.
- Multi-agent emergent systems underperform single-agent on SWE-bench Verified (G7 62.2% vs
  G6 73.2% — secondary source, unverified primary; Martinez et al. 2025 via emergentmind).
  Counterpoint: Trae Agent (multi-model) ranks #2 at 75.2% (secondary source, no cost data).
  This design is a single orchestrated pipeline, not multi-agent — but treat both as weak
  signals in opposite directions.
- OpenHands ships per-role model configs — a shipped industry pattern without published
  cost-benefit benchmarks (docs.openhands.dev).
- The 2-tier optimality finding guides our ESCALATION LADDER (2 metered rungs, §5). Whether
  functional role allocation (planner/executor/scout/QC) justifies more tiers than difficulty
  cascading is an untested engineering inference; §10 controls for it with a 2-tier variant
  (config D) and a flat-rate-planner variant (config E).

### Not established anywhere (we generate this evidence)
- Static role→model allocation with test-gated escalation vs single-model on
  cost-per-resolved-task in multi-turn agentic coding.
- Flat-rate/metered model interaction in cascades (config E, §10.4, is the first test).
- GLM-5.2's competence as a session-level orchestrator (turn-inflation ratio vs K3) — the
  single biggest unmeasured assumption in this design. §10 reports orchestration token share
  as a first-class metric with a 40% adoption guard (§7).

### Superseded premise
The prior dynamic-routing result is **uninterpretable on cost**, not falsified. The
scorecard's cost metric was a broken proxy (`tokens_reported: null` throughout in
`evals/results.jsonl`; `est_tokens` are byte-proxy estimates in the low hundreds, not real
token counts). All 18 showcase runs passed acceptance and hidden tests at 1.00 — fixtures
never discriminated on quality. Retroactive real metering (`evals/results-metered.jsonl`)
shows C at +16% and D at +37% vs A on real cost (config medians across all showcase runs).
The dynamic design's pre-registered magnitude predictions (≥2×/≥3×) did not materialize. The
static design rests on the pre-generation-routing evidence above (§2 Established), not on a
falsification claim. §10.1 re-metering may reverse the prior verdict — pre-registered: if
real metering shows the dynamic cascade actually won on cost, the static design still rests
on different evidence (pre-generation allocation) and proceeds.

## 3. Roles, allocation, and real pricing

| Role | Model | Transport | $/1M in (cached) / out |
|---|---|---|---|
| Planner / scoper (default) | Kimi K3 | native subagent (`model="secondary"` after flip) | 3.00 (0.30) / 15.00 |
| Planner / scoper (config E) | Codex CLI or Claude CLI | delegate `codex-advisor` / `claude-advisor` (flat-rate, read-only) | flat-rate (quota-bound) |
| Orchestrator + QC (session leader) | GLM-5.2 | primary session model | 1.40 (0.14) / 4.40 |
| Executor (default) | DeepSeek V4 Flash | delegate `ds-flash-worker` | 0.14 (0.028) / 0.28 |
| Executor (upgraded — requires planner justification) | DeepSeek V4 Pro | delegate `ds-pro-worker` | 1.74 (0.145) / 3.48 |
| Specialist (vision / code-specific) | Kimi K2.7-code | delegate `k27-worker` | 0.95 (0.19) / 4.00 |
| Apex advisor | Codex CLI (gpt-5.6-sol), Claude CLI | delegate `codex-advisor`, `claude-advisor` | flat-rate (quota-bound) |

Rules:
- **Default executor is Flash.** `pro` requires the planner to state a specific reason: no
  deterministic verifier available, cross-file reasoning required, or prior flash failure on
  the same subsystem. This directly tests the flash-first-try-rate hypothesis (§11) instead of
  assuming it away. (§0 cost model shows Pro only beats a COLD K3 session; Flash wins
  regardless.)
- GLM-5.2 and DS V4 Pro are the same price band — NOT ladder rungs. GLM runs the session,
  QC, and long-context analysis (note: GLM serverless context is **estimated** at ~200K —
  only K3's 192K serverless context is explicitly published; this is a GUESS, not confirmed;
  the >200K routing criterion is therefore >128K on standard serverless); Pro executes when
  Flash is insufficient.
- K3 (serverless context 192K, published) plans and judges only. The planner receives
  scope-limited context/summaries, never full repo dumps. When K3 is reached as an executor
  via escalation, it runs in a FRESH context with the evidence packet, without its planning
  context.
- Routine QC review uses Pro (cheaper output than GLM); GLM fresh-context review is reserved
  for high-criticality or >128K-context reviews.
- Native subagents after the config flip default to `secondary` = K3 (expensive!). Every
  native subagent invocation must pass an explicit model: planner → `model="secondary"`;
  GLM review/explore → `model="primary"`. The skill must state this verbatim.
- Dropped: grok-worker (Grok CLI uninstalled itself from this machine mid-project; the
  wrapper's validation is now lazy so a missing unrelated CLI can't block runs), glm-worker
  delegate profile (GLM is the session leader, not a delegate worker), minimax-m3,
  gpt-oss-120b (no distinct role).
- `write_allowed` in agents.json is metadata for orchestration policy only — NOT enforcement
  (the child CLI has the caller's filesystem access). Write containment is post-hoc: the
  orchestrator inspects every diff and rejects out-of-scope changes. **The one containment
  that IS enforced today:** `delegate/agents.json`'s `allowed_workspace_roots` — the wrapper
  validates workspace paths against this list with `Path.resolve(strict=True)` and rejects
  escapes (symlinks, junctions, sibling-directory spoofing). This is the load-bearing
  filesystem boundary; §8 extends it with git checkpoints and worktree isolation.

## 4. Plan-time allocation protocol

For a cascade-eligible goal (§1.6 gate), the orchestrator (GLM) invokes the planner (K3,
fresh isolated context, `model="secondary"`) once. Planner input: goal statement, scoped repo
orientation (file tree + key manifests, not full content), and the assignment rubric. In
config E (§10.4), the planner is `codex-advisor` or `claude-advisor` (flat-rate, read-only,
emits the same cascade-state.json schema); K3 planner is the fallback when advisors are
quota-limited.

Planner output — a task list serialized into `.orchestrator/cascade-state.json`. The
controller (`cascade.py`, §12) JSON-schema-validates this before any dispatch:

```
{ "goal": ..., "created_at": ..., "k3_direct_cost_estimate_usd": <planner's estimate — logged
    as calibration data point, NOT used as the cost ceiling (§7)>,
  "verifier_set": ["test/path1", "test/path2", "lint/config", ...],  // FROZEN at plan time (§6)
  "tasks": [ { "task_id", "objective",
               "executor": "flash|pro|k27|k3",          // default = flash; pro requires reason
               "pro_reason": "<specific reason or null>",
               "verification": {"deterministic": "<command>"} | {"qc_review": true},
               "scope": ["allowed/paths"], "criticality": "normal|high",
               "max_attempts": 2, "attempts": 0, "rung": 0, "status": "ready",
               "checkpoint_ref": null,                   // git ref recorded before each dispatch (§8)
               "qc_reviews": 0,                          // QC counter (cap 2 normal / 3 high)
               "resume_session_id": null }],             // for within-rung resume retries (§9.7)
  "counters": { "executor_attempts": 0, "qc_reviews": 0,
                "planner_calls": 1, "replan_calls": 0,   // cap 2/goal total
                "advisor_calls": 0 },                     // quota-tracked weekly
  "cost_used_usd": 0.0, "cost_ceiling_usd": <from history or user>, "cost_warning_usd": <0.5 × ceiling> }
```

Assignment rubric: `flash` = default for all tasks (mechanical, bulk, bounded implementation,
scouting, bulk reads); `pro` = bounded implementation requiring cross-file reasoning or no
deterministic verifier or prior flash failure on the same subsystem (planner must state
`pro_reason`); `k27` = vision or code-specialist need; `k3` = planner-marked critical
(security, concurrency, data-loss) — rare by construction. Verification commands are inferred
from project conventions (the planner has no shell), then validated by the orchestrator before
first use.

`cascade-state.json` is the source of truth across context compaction: the orchestrator
re-reads it before every dispatch and the controller updates it atomically after every result.
(Compaction-amnesia is the documented biggest operational risk.)

## 5. Escalation ladder (2 metered rungs + planner + flat-rate apex)

```
assigned executor (flash|pro|k27)  →  k3 (fresh context)  →  codex/claude advisor (flat)
```

Per-rung attempt budget: `max_attempts` (default 2) RESETS on each escalation. "Retry" =
re-invoke the same executor profile with the failure evidence appended to the task packet.
The delegate wrapper is stateless by default; §9.7 measures whether `kimi -r <session-id>`
resume yields cached input for within-rung retries — if so, the wrapper gains
`resume_session_id` plumbing and retries reuse warm prefixes at cached rates. Escalation to a
different model stays fresh by necessity (different context, different session).

Two verification paths, stated separately:
- **Deterministic path:** verifier fails → re-invoke same executor with evidence (counts
  toward `max_attempts`) → verifier still failing at `max_attempts` → escalate to K3 with
  evidence packet → K3 fails `max_attempts` → flat-rate advisor → orchestrator adjudicates
  advice and either retries K3 once with the advisor's input (the post-advisor K3 retry, §7
  budget) or stops and reports.
- **QC path (qc_review tasks, and high-criticality deterministic tasks):** QC rejects with
  structured findings (severity/location/claim/evidence/minimal fix) → one re-invocation with
  findings (counts toward `max_attempts`) → escalate.

Verifier-suspect escape hatch: if the SAME deterministic failure text recurs across two
different executors, the orchestrator opens a **verifier-defect state** (separate from the
task's execution state). The orchestrator reproduces the failure on the **untouched baseline**
(clean checkout of the checkpoint ref) to distinguish a broken verifier from a broken
implementation. If the verifier is judged broken, it is **repaired or explicitly waived with
user authorization** — the task is never converted to model-only acceptance. After repair or
waiver, deterministic acceptance is re-run. (Codex 7: never convert a failed deterministic
requirement into model-only acceptance.)

Replan trigger: if two tasks fail on the same root cause (decomposition error, not
implementation), the orchestrator re-invokes the planner to revise the task list. Planner +
replan calls are counted separately (cap 2/goal, §7) and do not count as worker invocations
but DO count toward the cost ceiling.

## 6. QC and acceptance

- **Verifier immutability (load-bearing):** at plan time the planner declares the frozen
  verifier set (`verifier_set` in cascade-state.json — test paths, lint/type configs, CI
  definitions). The orchestrator diffs every verifier file before accepting ANY task result.
  Any worker modification of a verifier file = **automatic reject + escalate**, never accept.
  The orchestrator re-runs verifiers itself (from the checkpoint ref's clean state); worker-
  reported test output is **never trusted**. This closes RouterBench's judge-error failure
  mode: a cheap executor that weakens tests to pass the gate is detected by the verifier-file
  diff, not by post-hoc judgment.
- Deterministic checks run first on every material diff; a failing check is never overridden
  by model judgment. A PASSING check on a high-criticality task is additionally reviewed by
  GLM fresh-context (reviewer sees requirement + diff + test output, never the worker's
  reasoning).
- qc_review rubric (when no deterministic verifier exists): reviewer checks requirement
  compliance, correctness, scope adherence (only `scope` paths changed), risk surface, and
  convention fit; verdict ∈ accept | accept-with-minor-fixes | reject with structured
  findings. Pass requires accept/accept-with-minor-fixes with no open blocker/major findings.
- Final acceptance (per goal): every task's verifier passed (deterministic, re-run by the
  orchestrator from the final combined tree) or QC verdict accepted; the orchestrator
  inspected the full diff; no files outside task scopes changed; no verifier file modified;
  limitations and unverified assumptions are stated to the user.

## 7. Budgets and observability

### Budget arithmetic

Separate counters, all fields in `cascade-state.json`:

| Counter | Normal criticality cap | High criticality cap | Scope |
|---|---|---|---|
| Executor attempts | 5 | 6 | 2 at assigned rung + 2 at K3 + 1 post-advisor K3 retry (normal); +1 more (high) |
| QC reviews | 2 | 3 | Per task; separate from executor attempts |
| Planner + replan calls | 2 per goal | 2 per goal | Across all tasks |
| Advisor calls | quota-tracked weekly | quota-tracked weekly | Subscription rate-limited, not a fixed cap |

**First cap hit stops the task.** The post-advisor K3 retry (1 invocation) is included in the
normal cap of 5: 2 (assigned rung) + 2 (K3 rung) + 1 (post-advisor K3) = 5. High-criticality
adds 1 more = 6. QC review invocations have their own cap and do not consume the executor
attempt budget. Planner/replan calls have their own cap and do not consume either.

### Cost ceiling

Ceiling = α × historical estimate, where α ≈ 0.6. The estimate is derived from **metered
history** (`evals/results-metered.jsonl`) by task class (e.g., simple_fix, mechanical,
multifile, debugging, security, exploration) — NOT from the planner's self-estimate. The
planner's `k3_direct_cost_estimate_usd` is logged as a **calibration data point** so estimator
accuracy becomes measurable over time. Warning at 50% of ceiling. If cumulative cost exceeds
the ceiling, stop and report. **Until sufficient history exists,** the user supplies the
ceiling per goal.

### Total-cost definition

Total metered cost = **ALL Fireworks usage**: GLM session turns, planner/replan calls,
executors, QC reviews, retries, cache-creation tokens, AND failed/aborted runs. A cascade that
fails expensively must not win on paper — failed goals count in the cost denominator. Cost per
completed task = total spend across all attempts (including failures and abandoned runs) ÷
independently accepted completions.

### Orchestration tax

The cascade-log distinguishes token classes: every invocation record carries
`token_class: orchestration|execution|qc|planning`. §10 reports orchestration share as a
first-class metric. **Adoption guard:** reject the cascade (config C) if orchestration tokens
exceed 40% of total — the design minimizes cost, not relocates it from executors to the
leader. **Batch dispatches** where possible: one orchestrator turn dispatching k tasks
amortizes the per-dispatch GLM overhead (at 30K in / 2K out uncached ≈ $0.051, ~5.5× the
entire $0.009 Flash worker call it dispatches).

### Observability

Every delegate/native invocation appends one JSON line to `.orchestrator/cascade-log.jsonl`:
timestamp, task_id, executor/model, `token_class`, tokens (uncached_in, cached_in, out),
est. cost, status, rung, trigger. After each task completion or escalation, the orchestrator
emits a one-line status: tasks done/total, invocations used, cost so far vs ceiling,
orchestration share. The user may abort between tasks; on interruption the orchestrator
reports state from cascade-state.json.

## 8. Infrastructure failures (not task failures)

- Transient (429/5xx/network/transport): retry once with backoff; does NOT count toward
  `max_attempts`, does NOT escalate.
- Wrapper timeout: counts toward `max_attempts` if the worker may have made partial progress;
  orchestrator inspects the workspace before any re-dispatch — **partial edits must be
  reverted or adopted deliberately** via the checkpoint ref (below).
- Worker session pruning at goal completion (headless `kimi -p` sessions accumulate).

### Checkpoints and write isolation

Before each dispatch, the orchestrator records a git checkpoint ref (commit or stash) in
`cascade-state.json` (`checkpoint_ref` per task). Rollback = `git checkout <ref> -- <scope
paths>`, reverting only the task's scope paths. This makes "revert partial edits" actionable.

Writers are dispatched in **isolated worktrees** for multi-writer or high-criticality goals,
following the workstation's central-git helpers (`New-CentralGitRepo.ps1` conventions —
worktrees in synced folders, git metadata under `C:\Dev\gitdirs`). The orchestrator promotes
only inspected patches from the worktree to the main working tree. Single-writer,
normal-criticality goals may dispatch directly into the working tree with the checkpoint ref
as the rollback mechanism.

The one containment enforced today is `delegate`'s `allowed_workspace_roots` (§3) — worktree
isolation extends this for the high-risk cases.

## 9. Harness changes

1. **Config flip** (`~/.kimi-code/config.toml`): `default_model = "fireworks/glm-5p2"`,
   `[secondary_model] model = "fireworks/kimi-k3"`. **USER CONFIRMATION REQUIRED; keep a
   rollback copy.** Note: this inverts the current pairing — existing sessions keep their
   original binding on resume, and anything passing `model="secondary"` today switches from
   GLM to K3. **Vision handling (v3.1 — replaces the "regression" framing; the cascade MUST
   handle images):** GLM/DeepSeek are text-only; pasting images into a text-only session can
   break it (FireConnect docs). The cascade handles vision via capability routing, not
   escalation (modality is orthogonal to complexity — an image task is impossible for a
   text-only model at any retry depth):
   (a) **Plan-time capability filter:** the planner marks any task touching image files or
   UI/screenshot review as vision-bearing → assigned to `k27-worker` (vision-capable,
   verified live 2026-08-13 via delegate: correctly described a test PNG) or K3 when
   critical. The controller enforces this at init: image-file extensions in a task's scope
   require a vision-capable executor.
   (b) **Interactive images = images-as-files mediation:** screenshots are saved to the
   workspace (never pasted into the leader session); the orchestrator dispatches them to
   `k27-worker` for analysis and consumes the textual result.
   (c) **Vision-heavy projects choose a vision leader:** for frontend/UI-heavy goals, start
   the session with `kimi -m fireworks/kimi-k2p7-code` (or K3) as primary instead of the
   GLM default. Leader-by-modality; GLM remains the default for text work.
   Note: K2.7-code ($0.95/$4.00) is cheaper than GLM ($1.40/$4.40) AND vision-capable — if
   the experiment shows GLM orchestration is weak, K2.7-primary is the documented fallback.
2. `delegate/agents.json`: rename `ds-pro-reviewer`→`ds-pro-worker`, add `k27-worker`
   (both with write_intended metadata; enforcement stays post-hoc diff inspection +
   checkpoint/worktree isolation per §8).
3. New skill `static-cascade` replacing the retired `multi-model-routing` skill
   (user-approved deletion of `~/.kimi-code/skills/multi-model-routing/SKILL.md`). The skill
   description must not force-load on trivial tasks (pilot defect). Skill text must state the
   explicit subagent model params (§3 rules).
4. `evals/meter.py`: parse per-run session `wire.jsonl` usage records — ACTUAL fields are
   `inputOther`, `output`, `inputCacheRead`, `inputCacheCreation`; model attribution requires
   correlating with model-set events in the same wire file. **Model-attribution reliability is
   an explicit §10.1 exit criterion:** if attribution fails, §10.1 produces a total cost but
   not a per-role breakdown, and the orchestration-tax metric (§7) stays unmeasurable — this
   blocks the adoption decision, not just the per-role report. (Downgraded from "verified" to
   "fields confirmed; attribution to be demonstrated.")
5. `evals/pricing.yaml`: real Fireworks prices from §3 including cached-input rates. **Price-
   drift check:** before each experiment run, re-verify `pricing.yaml` against Fireworks docs
   (catalog churn is documented — e.g., Qwen3-8B deprecated May 2026, Qwen 3.6 Plus June 2026;
   a repricing can invert the ladder). Log the verification date and any price changes.
6. Prompt caching across delegate calls is ASPIRATIONAL until measured (no session-affinity
   mechanism in the wrapper); §9.7 measures actual cache-hit rates before we claim them.
   Batch inference (50% off) applies only to pre/post-cascade bulk work, never the critical
   path.
7. **Wrapper resume work item (measure before the decisive experiment):** measure
   `kimi -r <session-id>` resume cost on a retry (same task, evidence appended) vs fresh
   dispatch. If resume yields cached input (re-uses warm prefix at 5–10× discount), add
   `resume_session_id` plumbing to the wrapper (new `agents.json` field + `--resume` delegate
   flag) and use it for within-rung retries. Escalation to a different model stays fresh.
   This changes the cost of every retry in the ladder — measure it before §10.4.
8. **Controller component `cascade.py` (to be built):** owns cascade-state.json transitions,
   JSON-schema-validates the planner's task list, enforces caps/counters and legal escalation
   transitions, performs verifier-immutability checks (diffs verifier files before accepting
   results), and is the only path that invokes delegate in cascade mode. Scope minimally:
   caps, transitions, verifier-immutability check, atomic state writes. GLM decides
   classification/QC within legal transitions; the controller rejects illegal ones.

## 10. Validation plan (meter first — before any further build)

1. **Retroactive metering (free, decision gate):** build `evals/meter.py`, meter the existing
   18 showcase runs → real cost table for A/C/D. **Exit criterion:** model attribution must
   produce per-role breakdowns (§9.4). If attribution fails, the adoption decision is blocked
   (the orchestration-tax metric is unmeasurable without it). If the dynamic cascade configs
   did NOT actually win on real tokens either, record it and proceed — the static design
   stands on different evidence (pre-generation allocation). Pre-registered: if real metering
   reverses the prior verdict (dynamic won on cost), the static design still proceeds on its
   own evidence.
2. **Config-flip canary:** backup config → flip → `kimi doctor` → headless GLM-primary
   trivial task end-to-end → headless GLM-primary session invoking K3 secondary planner
   subagent → verify model attribution in wire.jsonl.
3. **Static-policy canary:** one pilot case (bulk_migrate) under the static cascade with
   metering: plan carries assignments, ladder behaves, cost logged, cascade-log written with
   `token_class` fields, verifier-immutability check exercised.
4. **Decisive experiment:** one real task from the user's actual work where K3-alone is known
   expensive/slow. Configs:

   | Config | Description |
   |---|---|
   | A | K3-direct baseline |
   | B | GLM-primary alone (no cascade) |
   | C | Full static cascade (K3 planner, flash-default, 2 metered rungs + advisor) |
   | D | 2-tier control (flash+k3 only, no Pro tier) |
   | E | Flat-rate planner cascade (advisor planner, same cascade-state.json schema, K3 fallback) |

   **Replication:** ≥3 reps per config per task where feasible; report per-task medians (reuse
   `evals/report.py`). **Metrics:** total metered cost (§7 definition — all Fireworks usage
   including failures), acceptance per §6, escalation count, orchestration token share,
   flash-first-try rate, wall-clock (secondary, soft guardrail ≤2× A).

   **Rate hypotheses (flash-first-try ≥70%, escalation ≤30%) are OBSERVABLES with wide error
   bars, not gates** — only cost-at-equal-acceptance is decidable at this N. Pre-registered:
   C adopted only if `cost(C) < cost(A)` AND `cost(C) ≤ cost(B)` at equal acceptance (both
   pass the same verifier or same QC verdict).

   **Non-adoption paths (pre-registered):**
   - C beats A but loses to B → **leader swap alone is the win, drop the cascade** (GLM-primary
     without the cascade harness is the adoption).
   - D ≈ C → **drop the Pro tier** (2-tier flash+k3 is sufficient; Pro's cache-dependent
     advantage does not materialize in practice).
   - E ≈ C on acceptance → **E strictly dominates on metered cost** (flat-rate planner
     eliminates the per-goal K3 planner tax; §1.6 gate becomes unnecessary).

   **Task selection:** pre-register representative real-task strata including likely LOSSES
   (not only tasks where K3 is known expensive — favorable-selection bias is noted). Keep a
   holdout set. N=1–3 real tasks; treat as decision evidence, not statistical proof. QC
   evaluation where no deterministic verifier exists uses one fixed rubric applied identically
   across configs (not per-config reviewer calibration).

## 11. What we do not claim

- No published benchmark proves this architecture wins on cost-per-resolved-task in agentic
  coding; the constituent evidence supports the parts, the combination is untested. §10 is
  how we find out.
- Thresholds (max_attempts=2, caps 5/6, 2 advisors, α≈0.6) are engineering defaults consistent
  with collapse-prevention evidence, not empirically derived constants.
- **Break-even conditions are model-derived estimates**, not measured constants: under the §0
  cost model with assumed cache-hit rates, the cascade wins when the goal is ≥3
  K3-equivalent turns, flash-first-try rate ≥70%, and escalation rate ≤30%; it loses below
  those conditions. These thresholds will be calibrated against §10 measurements — until
  then they carry the uncertainty of the cache-rate assumptions behind them.
- GLM-5.2's serverless context (~200K) is a GUESS — only K3's 192K is explicitly published.
  The >128K routing criterion is a reasonable inference from this guess, not a measured
  boundary.

### Known gaps (rejected / deferred findings)

- **Per-call preventive cost reservation (Codex 2, rejected):** kimi-code does not expose
  per-call max-output control cleanly; reactive ceiling + per-task caps carry the risk for
  now. Revisit if the harness gains per-call output limiting.
- **Full OS-sandboxing of workers (Codex 3, rejected):** AppContainer/restricted-token
  isolation is out of scope for Phase-1. Worktree isolation + `allowed_workspace_roots` carry
  the containment risk. Revisit if a worker causes a containment breach that diff inspection
  + checkpoints cannot recover from.

## 12. Addendum: leader-context and worker-integrity fixes

The adjudicated follow-up plan `policy/leader-context-and-worker-integrity.md` (v2,
2026-08-13) extends this spec with: deterministic log extraction with coverage manifests
(`scripts/extract_log.py`, F1), evidence-bearing dispatch records (files_changed incl.
untracked inventory, child_exit_code, run_dir log archival with sha256, F2/F3),
controller-gated `commit-green` and scoped `rollback` (F6/F7), owner-set immutable
threat models with verbatim dismissed-finding records (F8), non-discretionary spot
verification (F5), and session-rebuild cost discipline (`handoff`, decisions log,
`meter.py --rebuild-watch`, F9). It is authoritative for those mechanisms; this spec is
not duplicated there.
