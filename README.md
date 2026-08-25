# model_proctor

A deterministic control plane for coding agents on [Kimi Code CLI](https://github.com/MoonshotAI/kimi-cli):
task-owning workers dispatched through a thin runner, leader-executed verification with tree-bound
acceptance receipts, wire-metered cost accounting, and a pre-registered evaluation harness.

**The name.** A *proctor* assigns the exam, watches the clock, and grades it objectively — the model
never marks its own work. That is the architectural invariant this repo converged on: deterministic
evidence outranks every model, and acceptance is bound to the exact tree that was verified.
(Historical names: `robot_lockstep_ballast` — *lockstep* for fixed rungs, *ballast* for the cheap
fleet — then briefly `kimi_router`; renamed 2026-08-25. *Static cascade* remains the name of the
frozen research **pattern** documented here; `runner/` is the live implementation path.)

---

## What this repo is

- `runner/` — **the live control plane** (`runner.py`): task intake with observable features, a
  frozen lane table, worker dispatch through the delegate wrapper, leader-executed verification,
  tree-bound acceptance receipts (stale on any post-verify mutation), a sealed verification
  surface (payloads copied out of the agent-writable workspace at init; tampered inputs are
  restored and flagged at verify time), stagnation detection with failure-class switching, and an
  append-only metered task ledger. Runner state lives **outside** the workspace
  (`.runner-state/` sibling). Smoke suite S1–S7: `python -m unittest discover -s runner/tests -v`.
- `delegate/` — a lean Windows-native subprocess wrapper (`delegate.py`) that runs external CLI
  workers with a stable envelope contract: exit codes, truncation flags, captured child session
  ids, and per-dispatch isolated+seeded `KIMI_CODE_HOME` homes (`child_home` in the envelope;
  callers meter from it, then delete it). 90 tests.
- `cascade/` — the deterministic static-cascade controller (`cascade.py`), **frozen research
  artifact** (governance: issue #16). Owns `cascade-state.json` transitions, plan validation,
  caps and legal escalation transitions, evidence archival, `commit-green`/`rollback` git gates,
  `handoff`/`record-decision` continuity, threat-model field, vision capability filter. Carries
  known trust-boundary defects (issues #17–#22) — do not use its green gate as acceptance
  authority; `runner/` designs them out instead of patching them. 76 tests (2 known
  environment-sensitive failures, documented in #20).
- `scripts/extract_log.py` — deterministic session-log fact extractor with a **coverage manifest**
  (bytes in, records parsed, records dropped). LLMs interpret extractions; they never scan raw
  volume. Verifier-class: hash-frozen per goal. 5 tests.
- `policy/` — the governing specs (see Provenance below).
- `evals/` — the benchmark harness: v1 (20 cases + 2 showcase, killed the dynamic design), v2 and
  v3 quality sets (10 each, naive-solution-proven to discriminate requirement fidelity), the
  metering stack (`meter.py`, `pricing.yaml`), pre-registrations (`PREREG-v2.md`, `PREREG-v3.md`),
  and every result row (`results*.jsonl`, `pilot-*.jsonl`, `phase2-*.jsonl`, `phase3-*.jsonl`).
- `skill/` — installable Kimi Code skills (`task-router/` live policy, `static-cascade/` frozen
  reference, `multi-model-routing/` superseded).

Python 3.10+, standard library only, everywhere.

```bash
python -m unittest discover -s runner/tests -v    # live path smoke suite (S1–S7)
python -m unittest discover -s delegate/tests -v
python -m unittest discover -s cascade/tests -v
python -m unittest discover -s scripts/tests -v
```

---

## Provenance: how this design was arrived at

This section exists because the final design only makes sense as the residue of several
**falsified or adjudicated predecessors**. Each phase is documented in this repo.

### Phase 0 — Reverse-engineering Magnitude (2026-07)

Starting question: can an open-model harness reproduce [Magnitude](https://magnitude.dev/)'s routing
advantages — better-than-single-model quality at lower cost — inside Kimi Code CLI?

`magnitude_reverse_engineered_routing_scaffold.md` is the reconstruction, adversarially reviewed by a
5-critic swarm (source-fidelity, routing-theory, cost, security, and operations critics). Its durable
conclusions:

- Magnitude's defensible advantage is **not a learned model selector**. It is a hierarchical,
  stage-aware orchestration policy: persistent leader → bounded subtasks → specialized worker roles →
  isolated contexts → mandatory fresh-context independent review → bounded escalation.
- **Confirmed** from public material: leader/worker split, role specialization, isolated contexts,
  fresh-context review, lower-cost workers for token-heavy tasks, provider capability metadata.
- **Not established** (and never claimed again): learned routers, bandit routing, online learning,
  per-repo auto-benchmarking, exact thresholds.

### Phase 1 — Operationalization specs (2026-07)

Two independent proposals for kimi-code-cli:

- `kimi_k3_glm_external_workers_final_plan.md` — two-tier architecture: Kimi K3 lead orchestrator,
  GLM-5.2 native secondary workers, external CLI workers (DeepSeek, others) behind a lean `delegate`
  subprocess wrapper. Key surviving decision: **do not make the wrapper impersonate the
  secondary-model API** — a one-shot subprocess result is not a streaming model endpoint; faking one
  requires an HTTP service and defeats the purpose.
- `Kimi Code Multi-Model Orchestration Scaffold.pdf` — a parallel proposal, model lineup updated for
  the current Fireworks catalog.

### Phase 2 — Dynamic per-turn routing: built, benchmarked, **rejected** (2026-07/08)

The first implementation was a **dynamic** router (`skill/multi-model-routing/`,
`policy/delegation-policy.md`): the leader classified each task at dispatch time and picked a model,
with pre-registered predictions of **≥2–3× cost reduction** vs K3-alone.

`evals/` is the harness built to test that: 20 pre-registered cases (tune/holdout split fixed before
any runs), deterministic fixtures, machine-checkable acceptance **plus hidden tests**, blinded
scoring (`report.py` never reveals which config is the router until the key is read), and a
simple-case guardrail (the router must not regress trivial tasks).

**What the benchmark actually showed** — and this is why honest instrumentation matters:

1. **The primary cost metric was a broken proxy.** `tokens_reported` was null throughout (kimi prints
   no token counts in headless mode), so cost fell back to an I/O-volume heuristic.
2. **The fixtures never discriminated on quality.** All 18 showcase runs passed acceptance and hidden
   tests at 1.00 across all configs — the suite could not detect quality differences at all.
3. **Retroactive real metering** (`evals/results-metered.jsonl` — true per-model token usage pulled
   from session wire logs, priced by `evals/pricing.yaml`) showed the routing configs at **+16% and
   +37% real cost vs the K3 baseline** (config medians across all showcase runs).
4. The simple-case guardrail **failed** (routing overhead slowed the trivial case).

The dynamic design's cost predictions did not materialize; on cost the prior result is
*uninterpretable* (broken proxy), not falsified — but nothing justified adoption, and the guardrail
failure was real. **Dynamic per-turn routing was rejected.** The skill is kept in-repo as a
superseded artifact, not deleted, because the delegation mechanics (wrapper, task packets, evidence
rules) survived into the static design.

### Phase 3 — Literature verification → the static cascade (2026-08)

Before designing v3, the routing literature was checked against primary sources. The evidence base
(`policy/STATIC_CASCADE_SPEC.md` §2, each claim labeled established / partially-established /
implementation-choice):

- **Pre-generation (plan-time) routing beats post-generation cascading on 4 of 5 benchmarks** —
  cascades pay the cheap model's structural cost on every escalation. All single-turn benchmarks;
  multi-turn agentic transfer untested. (arXiv:2605.06350)
- **2-tier difficulty cascades are optimal**; extra mandatory tiers add cost without held-out gains.
  (arXiv:2605.06350)
- **Cascading only beats baseline when verifier/judge error ≤ 0.1**; at 0.2 performance may
  deteriorate rapidly. (RouterBench, arXiv:2403.12031)
- **Deterministic verification (code execution) as the escalation signal works in agentic settings**:
  EcoAssistant, +10pts success at <50% cost. EcoAssistant's escalation is dynamic; this design borrows
  its verifier mechanism but replaces dynamic routing with static plan-time allocation.
  (arXiv:2310.03046)

The decisive internal analysis was the **cost model** (spec §0): agentic coding is input-dominated,
and the delegate wrapper's stateless calls **forfeit prompt-cache state**. Worked arithmetic (60K
input / 3K output turn, Fireworks list prices):

| Path | Turn cost |
|---|---|
| K3 warm session (80% cache hit) | $0.095 |
| **DS-Pro via stateless delegate (0% cache)** | **$0.115 — loses to warm K3** |
| K3 cold (0% cache) | $0.225 |
| **DS-Flash via stateless delegate** | **$0.009 — wins regardless (~10–25×)** |

Hence the inverted default: **Flash is the default executor**, Pro is escalation-only, K3 plans,
GLM-5.2 orchestrates and QCs, Codex/Claude CLI subscriptions sit at the apex as flat-rate read-only
advisors. Allocation is decided once at plan time and **controller-enforced** — GLM decides within
legal transitions; the controller rejects illegal ones. GLM is a decision component, not the control
plane.

The spec went through two expert advisor reviews (Codex + Claude); v3 incorporated 15 findings and
rejected/deferred 2 with reasons recorded (spec §11). Current version: v3.1.

### Phase 4 — Production dogfooding on a real financial-controls codebase (2026-08)

The cascade was taken into a live takeover of an independent quant-engine project (private repo) —
real gates (824-test suite, strict mypy, ruff), a governed issue tracker, adversarial QC on every
material change. What production taught that benchmarks didn't:

- **The leader's own context is the dominant cost**, not workers. Metered reality on the heaviest day:
  ~$203 total, of which the K3 leader session was ~$196 (96%) — 506M cached-read tokens compounding
  over an all-day session — vs $7.23 for all delegate workers combined. The counterfactual for the
  delegated slice: ~$9.44 as-routed vs ~$17.85 had the same work run on K3.
- **Summaries crossing the worker→leader boundary can miss, lose, or misrepresent.** The response is
  architectural, not more review layers: deterministic log extraction with coverage manifests,
  controller-computed `files_changed` (including untracked files), evidence archival with sha256 at
  dispatch completion, truncation flags that void verdicts, and non-discretionary spot-verification.
- **Self-set review standards are a governance hole.** `threat_model` is now an owner-set field at
  `cascade init`, immutable for the goal; dismissed blocker/major findings must be recorded verbatim
  and surface verbatim in the final report.
- **Adversarial QC loops need hard caps.** One composite change went through 8 fresh-context QC
  rounds; the controller's code-enforced QC cap exists because a human leader will not stop itself.

These lessons are codified in `policy/leader-context-and-worker-integrity.md` (v2 — itself
adversarially reviewed by 5 fresh-context reviewers, which caught v1 overstating what existed; every
fix is honestly labeled [CODE] / [POLICY] / [RESIDUAL]).

### What remains unsolved (honest residuals)

- Entailment and completeness are not boundary-checkable: omission, true-but-irrelevant evidence,
  and aggregate miscomposition survive every guard. Reduced, not eliminated; still a leader/human duty.
- Break-even conditions in the spec are model-derived estimates under assumed cache-hit rates, not
  measured constants. `evals/meter.py` (`session_rebuild` token class) exists to measure them before
  further adoption claims.

---

## Reference research

| Claim used | Source |
|---|---|
| Pre-generation routing > post-generation cascading (4/5 benchmarks); 2 tiers optimal | arXiv:2605.06350 |
| Cascade viability requires verifier error ≤ 0.1 | RouterBench, arXiv:2403.12031 |
| Deterministic verification as escalation signal in agentic settings | EcoAssistant, arXiv:2310.03046 |

## Reference products

- [Magnitude](https://magnitude.dev/) — the routing behavior this project set out to reproduce with
  open models; see Phase 0 for what is confirmed vs. inferred about it.

## Model lineup (measured, 2026-08-25 — replaces the frozen design's assumed ladder)

The frozen cascade's role ladder (K3 plans → GLM orchestrates → Flash executes → Pro/advisors
escalate) was a **cost hypothesis**. The pre-registered fixed-arm grids (issues #27/#29/#30,
`evals/phase2-*.jsonl`, `evals/phase3-*.jsonl`) measured it instead: on bounded, spec-complete
tasks the arms are quality-indistinguishable, so cost decides.

| Role | Model | Basis |
|---|---|---|
| Default worker (bounded/substantial) | GPT-OSS-120B (`gpt-oss-worker`) | 29/30 hidden on v3 (one flake), $0.053/hidden-pass — 3.0× cheaper than K3, zero systematic discordance |
| Second (equal quality, ~1.8× faster wall) | GLM-5.2 (`glm-worker`) | 30/30 hidden on v3, $0.097/hidden-pass |
| Marathon / open-ended | Kimi K3 (`k3-worker`) | **Hypothesis only — see Open gaps below** |
| Cheap scout (vision, misc.) | Kimi K2.7 (`k27-scout`) | Carried from the frozen roster; live |
| Advisors (read-only, closed triggers) | Codex CLI / Claude CLI | Flat-rate; trigger set in issue #8 — **not yet wired into the runner** (see Open gaps) |

Retired: DeepSeek V4 Flash (404 on Fireworks since 2026-08-25, see #23). DS-Pro is a benchmark
challenger, not a rung.

---

## Development process: the backlog is GitHub Issues

All work on this tool is managed through **this repository's GitHub Issues** — no side channels.

- Every unit of work is an issue with a stable key in the title (`[AREA-NNN]`).
- Labels: `priority:now` / `priority:next` / `priority:parked`, plus `type:*` and `area:*`.
- Dependencies are tracked in a `## Blocked by` section (`- #N` lines or `None`). Parked items carry
  an explicit **activation trigger** so overkill stays parked instead of silently becoming urgent.
- Closing an issue requires a non-empty `## Final evidence and handoff` section — evidence, not vibes.
- Design decisions that survive debate are recorded in `policy/` with rejected alternatives and
  rationale; session-boundary handoffs go through `cascade.py handoff` + the append-only decisions log.

Start here: [Issues](../../issues).

---

## Status

The measured end-state for bounded/spec-complete tasks is live: fixed cheap task-owning worker +
deterministic verification + failure-class switching, no economic routing. The bespoke cascade is a
frozen research artifact (6095695; governance #16). The grid results (#29/#30): quality parity
across K3/GLM/GPT-OSS at 3.0× cost spread → the STOP rule fired; routing complexity stays parked.

The trust boundary is sealed for the live path (TOOL-013/014, issue #31/#33): isolated per-dispatch
`KIMI_CODE_HOME`, runner state and verifier payloads outside the agent-writable workspace,
restore-and-flag on verifier tamper, tree-staled receipts.

### Open gaps and their activation triggers

Parked items stay parked until their trigger fires — that is the governance working, not neglect.

| Gap | Status | Activation trigger |
|---|---|---|
| **Marathon / open-ended lane (K3)** | Unproven — no v2/v3 case is marathon-shaped; K3's role there is a vendor-prior hypothesis | The first real multi-hour/open-ended task appears, or a marathon-shaped corpus gets built (extend `evals/fixtures/`, new PREREG); then run K3-vs-cheap as fixed arms on it before trusting the lane |
| **Advisor gates (Sol/Opus, #8) and M3 parallelism (#26 Phase 5)** | Deferred, untested | Observed *conceptual* stagnation in production — workers stuck on reasoning, not execution loops (the runner's failure-class log distinguishes these); for M3: a task with genuinely separable uncertainty branches |
| **Provider-billing reconciliation** | Residual — wire-metered costs are the estimate of record | First provider invoice day, or Fireworks billing-API access: reconcile `evals/meter.py` output against the bill within the PREREG tolerance (2–5%) |
| **Decision-grade evaluation** | Screens only (v2/v3 are n=10–30, hidden checks run in-workspace albeit sealed) | Any claim intended to justify production adoption for a *third party*; until then, all results here are single-operator screens |
| **Learned routing (#7)** | Parked | Measured oracle headroom: fixed arms must show systematic per-task-class quality splits large enough to pay for a classifier — v2/v3 show the opposite |
