# robot_lockstep_ballast

A static-cascade multi-model routing harness for [Kimi Code CLI](https://github.com/MoonshotAI/kimi-cli):
fixed-role allocation decided once at plan time, enforced by a deterministic controller, with cheap
metered models carrying the bulk of the work and expensive models reserved for what measurably needs them.

**The name.** *Lockstep* — ranks are fixed, escalation is one rung at a time, nothing skips. This is the
behavioral property that distinguishes a static cascade from a dynamic router. *Ballast* — the cheap
metered fleet (DeepSeek V4 Flash/Pro, Kimi K2.7) carries the load so the expensive leader stays stable
above. *Static cascade* remains the name of the **pattern**; this repo is one implementation of it.

---

## What this repo is

- `cascade/` — the deterministic static-cascade controller (`cascade.py`). Owns `cascade-state.json`
  transitions, JSON-schema-validates the planner's task list, enforces caps/counters and **legal
  escalation transitions only**, archives dispatch evidence (`files_changed`, run-log archival with
  sha256), and provides `commit-green` / `rollback` git gates, `handoff` / `record-decision` session
  continuity, an owner-set immutable `threat_model` field, and a vision capability filter.
  76 tests.
- `delegate/` — a lean Windows-native subprocess wrapper (`delegate.py`) that runs external CLI
  workers (fast scouts, cheap workers, independent reviewers, read-only advisors) with a stable
  envelope contract: exit codes, truncation flags, captured child session ids for cheap resume.
  88 tests.
- `scripts/extract_log.py` — deterministic session-log fact extractor with a **coverage manifest**
  (bytes in, records parsed, records dropped). LLMs interpret extractions; they never scan raw
  volume. Verifier-class: hash-frozen per goal. 5 tests.
- `policy/` — the governing specs (see Provenance below).
- `evals/` — the A/B/C benchmark harness that killed the dynamic design (see below).
- `skill/` — installable Kimi Code skills (`static-cascade/`, `multi-model-routing/` (superseded)).

Python 3.10+, standard library only, everywhere.

```bash
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

## Model lineup (Fireworks, metered) and advisors (flat-rate)

| Role | Model | Why |
|---|---|---|
| Planner (per substantial goal) | Kimi K3 | Planning/decomposition quality; a fixed per-goal tax, gated away from trivial tasks |
| Orchestrator / QC | GLM-5.2 | $4.40 vs $15.00 per 1M output vs K3; decides within controller-legal transitions |
| Default executor | DeepSeek V4 Flash | ~10–25× cheaper than K3 per turn; wins regardless of cache state |
| Escalation executor | DeepSeek V4 Pro | Only on evidence-driven escalation (deterministic verification first, QC judgment second) |
| Advisors (read-only, apex) | Codex CLI / Claude CLI | Flat-rate subscriptions; adversarial QC and design review; never edit the workspace |

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

Research artifact frozen at 6095695 (2026-08-13); governance: issue #16 — the bespoke cascade
carries no production authority. The wrapper, controller, and extractor are test-covered and
installed via `scripts/install.py`. The benchmark suite is retained as regression
infrastructure — its negative result is part of this repo's value.

The live experiment path is `runner/` (MVP-001, issue #27): a thin task-owning control plane
with tree-bound acceptance receipts, config-surface verification, and stagnation switching —
designing out the frozen cascade's #17–#20 defects instead of patching them. Policy:
`skill/task-router/SKILL.md` (installed alongside `static-cascade`). Smoke suite:
`python -m unittest discover -s runner/tests -v`.
