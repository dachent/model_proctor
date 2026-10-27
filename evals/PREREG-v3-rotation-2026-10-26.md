# PREREG-v3-rotation-2026-10-26 — re-measure the 2026-10-26 rotated arms on the v3 corpus

Date: 2026-10-26. Sealed by commit before any rotation-arm runs. Companion to
PREREG-v3-rotation (completed 2026-09-09, issue #91): same corpus, same fairness
contract, same harness — new arms only. PREREG-v3-rotation is untouched history.

## Why this exists

The 2026-10-26 rotation (owner-directed, PR #117) moved the flash lane to
`deepseek-v4p1-flash-1017` and the k3 lane to `ember-1`; both are unmeasured.
Additionally, the 2026-10-26 log review found the savings model's `b` (flash) and
`s` (strong) were measured 15 days apart in non-interleaved runs, and the
scorecard's adoption verdict was computed in tokens (fixed in this commit series).
This run measures both new flash and the glm comparator in ONE interleaved run so
`b` and `s` share a session, closing the cross-run error.

## Corpus

Unchanged: cases `q11_*` … `q20_*` (set `v3`, category `quality`), same fairness
contract (prompts state every requirement; `hidden_check.py` tests only stated
requirements).

## Arms (pinned to the live roster)

| Arm | Worker | Model | Lane |
|---|---|---|---|
| F | `marathon-worker` | `fireworks/deepseek-v4p1-flash-1017` | `--lane flash` |
| G | `substantial-worker` | `fireworks/glm-5p3` | `--lane glm` |

The k3 lane (`expert-worker` → `ember-1`) is NOT in this arm pair: its price row
equals kimi-k3's, and the break-even's k3 comparator is kimi-k3's measured rate.
Measuring ember-1's quality is a separate arm if wanted.

Fixed arms, `max_dispatches=1`, lane override, wire-metered via `record --pricing`
with the current pricing table (which now carries the 2026-10-26 rows), intention
to treat. **3 reps per case × arm = 60 runs**, fresh fixture workspace per rep.

## Interleaving (the change from v3-rotation)

Arms F and G alternate per case (F q11, G q11, F q12, G q12, …), both in the same
session on the same day. `b` and `s` therefore come from one run, not from runs 15
days apart.

## Metrics

Per arm: accepted, hidden-pass (run level, n=30), per-case rep pass-rates, flake
rate (mixed rep outcomes), all-in `api_cost_usd`, wall time, cost per
hidden-passing run. Systematic failure = a case failed all 3 reps.

## Decision rules (frozen)

1. **Quality screen (n=30):** a rotation arm is confirm-non-inferior if its
   hidden-pass count is ≥ 27/30 AND it has no systematic-failure case the glm
   comparator passed all 3 reps.
2. **Flake accounting:** mixed-rep cases reported separately, excluded from
   systematic-discordance counts.
3. **Cost rule:** rank arms by cost per hidden-passing run; report `b` (F) and
   `s` (G) to 4 decimal places and the b/s ratio, recomputed from this run only.
4. **Lane-policy decision input (owner's call, not auto-adoption):** report
   screen-confirmed; decision-grade needs the #16 sealed-evaluator build.
5. **Failure handling:** timeouts and dispatch failures count as failures and stay
   in the denominator. Provider storms recorded, not retried (intention to treat).

## Budget

Estimate ~$3–5 total (glm-5p3 ≈ its #91 rate; 1017 priced $0.30/$0.006/$1.20).
**Hard sanity ceiling: abort and report if metered spend exceeds $15** before all
60 runs complete.

## Integrity notes

- Same screen limitation as v2/v3: hidden checks live in the agent's workspace;
  not decision-grade (#16).
- Metering uses the current pricing table; raw usage rows are the evidence.
- The measured arm's $0.0096/hidden-pass and the 8.2x/17.0x ratios belong to the
  retired glm-5p3-flash / kimi-k3 arms and do NOT carry over.
