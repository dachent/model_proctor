# PREREG-v3-rotation — post-rotation re-measurement on the v3 corpus

Date: 2026-09-09. Sealed by commit before any rotation-arm runs (issue #91,
EVAL-006; owner approval recorded in-session 2026-09-09). Companion to
PREREG-v3 (completed 2026-08-25): same corpus, same fairness contract, same
harness and rules — new arms only. PREREG-v3 itself is untouched history.

## Why this exists

The 2026-08-28 Fireworks roster rotation replaced both measured lane models
before the PREREG-v3 STOP recommendation was implemented: the flash lane's
measured predecessor (deepseek-v4-flash-0731 — 30/30 hidden at $0.012/hidden-
pass, the best arm) was retired, and glm-5p2 (30/30 at $0.097) was succeeded
by glm-5p3. Every production dispatch since runs on unmeasured arms, while
the measured confirm-non-inferior cheapest arm (gpt-oss-120b, $0.053/hidden-
pass) sits rostered but unused as a lane.

## Corpus

Unchanged: cases `q11_*` … `q20_*` (set `v3`, category `quality`), same
fairness contract (prompts state every requirement; `hidden_check.py` tests
only stated requirements).

## Arms (new, pinned to the live roster)

| Arm | Worker | Model | Lane |
|---|---|---|---|
| D | `glm-worker` | `fireworks/glm-5p3` | `--lane glm` |
| E | `glm-flash-worker` | `fireworks/glm-5p3-flash` | `--lane flash` |

Serving probed 2026-09-09 (both dispatch OK). Fixed arms, `max_dispatches=1`,
lane override, wire-metered via `record --pricing` with the **corrected**
pricing table (PR #87 — the flash arm is metered truthfully for the first
time), intention-to-treat. **3 reps per case × arm = 60 runs**, fresh fixture
workspace per rep.

## Baselines (completed PREREG-v3 grid, `evals/phase3-2026-08-25.jsonl`)

A kimi-k3 30/30 @ $4.589 ($0.153/hp) · B glm-5p2 30/30 @ $2.916 ($0.097/hp)
· C gpt-oss-120b 29/30 @ $1.526 ($0.053/hp); retired flash-0731 30/30
($0.012/hp, `evals/phase3-flash0731-2026-08-25.jsonl`).

## Metrics

Per arm: accepted, hidden-pass (run level, n=30), per-case rep pass-rates,
flake rate (mixed rep outcomes), all-in `api_cost_usd`, wall time, cost per
hidden-passing run. Systematic failure = a case failed all 3 reps.

## Decision rules (frozen)

1. **Quality screen (n=30):** a rotation arm is confirm-non-inferior if its
   hidden-pass count is ≥ 27/30 (within 3/30 of the 30/30 reference arms) AND
   it has no systematic-failure case that baseline B passed all 3 reps.
2. **Flake accounting:** mixed-rep cases are reported separately and excluded
   from systematic-discordance counts.
3. **Cost rule:** among confirm-non-inferior arms (rotation arms and the
   08-25 baselines together), rank by cost per hidden-passing run on the
   corrected pricing.
4. **Lane-policy decision input (owner's call, not auto-adoption):** flash
   lane — glm-5p3-flash vs measured gpt-oss-120b; glm lane — glm-5p3 vs
   glm-5p2. Report as screen-confirmed; decision-grade needs the #16
   sealed-evaluator build.
5. **Failure handling:** timeouts and dispatch failures count as failures and
   stay in the denominator. Provider-failure storms are recorded, not
   retried (intention-to-treat; #75's circuit breaker is folded into #83 M1).

## Budget

Estimate ~$3-5 total at corrected prices (glm-5p3 ≈ B's $2.916; glm-5p3-flash
priced $0.15/$0.03/$0.50). **Hard sanity ceiling: abort and report if metered
spend exceeds $15** before all 60 runs complete.

## Integrity notes

- Same screen limitation as v2/v3: hidden checks live in the agent's
  workspace; not decision-grade (#16).
- Metering uses the repo pricing table with the 2026-09-09 Flash correction
  (PR #87); raw usage rows are the evidence, estimates derived from them.
