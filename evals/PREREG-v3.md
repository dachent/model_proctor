# PREREG-v3 — confirmatory fixed-arm run on the hard corpus

Date: 2026-08-25. Sealed by commit before any v3 arm runs (issue #30; owner decision (b)
on #29). Supersedes PREREG-v2 only in corpus, reps, and margins — arms and harness unchanged.

## Why v3 exists

The v2 screen (d86a0c3) produced one discordant pair at n=10×1 rep: K3 missed
`q9_rate_limiter`, both cheaper arms passed everything, STOP fired. Two threats to that
result: (1) the corpus was too easy to separate arms (all near ceiling); (2) single reps
cannot separate systematic failure from flake. v3 targets both: harder cases, 3 reps.

## Corpus

Cases `q11_*` … `q20_*` (set `v3`, category `quality`). Same fairness contract as v2:
prompts state every requirement; `hidden_check.py` tests only stated requirements.
Additional QA gate: a naive solution must pass `check.py` and fail `hidden_check.py`
(discrimination proof), reference solution passes both, skeleton fails both.

## Arms (unchanged, pinned)

| Arm | Worker | Model |
|---|---|---|
| A (reference) | `k3-worker` | `fireworks/kimi-k3` |
| B | `glm-worker` | `fireworks/glm-5p2` |
| C | `gpt-oss-worker` | `fireworks/gpt-oss-120b` |

Fixed arms, `max_dispatches=1`, lane override, wire-metered, intention-to-treat.
**3 reps per case × arm = 90 runs**, fresh fixture workspace per rep.

## Metrics

Per arm: accepted, hidden-pass (run level), per-case rep pass-rates, flake rate
(cases with mixed rep outcomes), all-in `api_cost_usd`, wall time. Paired discordance
at case level: a case counts as "arm X systematically fails" only if X fails all 3 reps
while another arm passes all 3.

## Decision rules (frozen)

1. **Quality screen (n=30):** arm is confirm-non-inferior to reference A if its
   hidden-pass count is within 3/30 of A's AND it has no systematic-failure case where A
   passes all 3 reps.
2. **Flake accounting:** cases where ANY arm shows mixed rep outcomes are reported
   separately and excluded from systematic discordance counts. K3's v2 `q9` miss is
   re-examined: v3 has no q9 rerun, but v3 flake rates calibrate how much weight a
   single-rep v2 miss deserves.
3. **Cost rule:** among confirm-non-inferior arms, rank by cost per hidden-passing run.
4. **STOP rule:** if the cheapest confirm-non-inferior arm exists with no systematic
   unique-solve case favoring a pricier arm, adopt it as the fixed default for this task
   class and STOP routing work (#26 Phase 3 stays closed). Report as
   "screen-confirmed", not decision-grade; a true decision-grade claim needs the
   #16 trust-boundary build (sealed evaluator outside the agent's writable tree).
5. **Corpus rule:** if all arms tie within the band AND no systematic discordance
   appears anywhere, v3 still does not discriminate — record that as the finding;
   the honest conclusion is then "these arms are indistinguishable on this workload
   class at current difficulty", which itself supports STOP-on-cost.

## Integrity notes

- Same known limitation as v2: hidden checks live in the agent's workspace (screen, not
  decision-grade; see #16). Runner receipts bind the tree; post-verify tampering stales
  acceptance.
- Arm C cost remains a lower bound (cached input billed at full input price).
- Timeouts and dispatch failures count as failures and stay in the denominator.
