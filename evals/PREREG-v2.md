# PREREG-v2 — Phase 2 fixed-arm screen on the quality corpus

Date: 2026-08-25. Sealed by commit before any arm is run (issue #29 / #26 Phase 2).

## Corpus

Cases `q1_*` … `q10_*` (set `v2`, category `quality`), generated deterministically by
`evals/fixtures/gen_q*.py`. Every prompt states the full requirement set; `check.py`
(visible acceptance) covers the happy path; `hidden_check.py` covers every stated
requirement including edge cases. Discrimination mechanism = requirement completeness.
QA gate (EVAL-001): reference solutions pass both checks; shipped skeletons fail both.

## Arms (fixed, no routing, no rescue — `max_dispatches=1`, lane override)

| Arm | Worker | Agent | Model (probed live 2026-08-25) |
|---|---|---|---|
| A | Kimi K3 (reference) | `k3-worker` | `fireworks/kimi-k3` |
| B | GLM-5.2 | `glm-worker` | `fireworks/glm-5p2` |
| C | GPT-OSS-120B | `gpt-oss-worker` | `fireworks/gpt-oss-120b` |

Out of scope: MiniMax M3 (not configured on this account); DS-Pro (held as challenger).
1 rep per case × arm = 30 runs. This is a screen; decision-grade n follows from measured
discordance, per #16's amendment of the sample-size bands.

## Metrics

Per arm: accepted (visible), hidden-pass, all-in `api_cost_usd` (wire-metered,
`usage.record` only), wall time. Paired solve-overlap matrix across arms.

## Decision rules (frozen)

1. **Quality screen:** an arm is screen-non-inferior to the reference arm (A) if its
   hidden-pass count is within 1 of 10 of A's AND its accepted count is within 1 of 10.
   n=10 cannot resolve smaller margins; ties within the band are ties.
2. **Cost rule:** among screen-non-inferior arms, rank by cost per hidden-passing task
   (tasks with zero hidden passes price against assigned count).
3. **STOP rule:** if the cheapest screen-non-inferior arm exists and paired overlap shows
   no case solved only by a more expensive arm's *unique* capability pattern worth more
   than the price delta, adopt the cheapest as the fixed default and STOP — no task-start
   routing work (#26 Phase 3 stays closed).
4. **Continue rule:** proceed to Phase 3 only if arms split the corpus (meaningful
   paired discordance) AND the cost spread between arms exceeds the measured rescue/switch
   overhead band from the MVP-001 smoke data.
5. **Corpus failure rule:** if all arms tie at 10/10 hidden, the corpus still does not
   discriminate at screen scale — record that, raise difficulty (v3), rerun. That is a
   corpus result, not a routing result.

## Integrity notes

- Hidden checks live in the same workspace as the agent (known limitation inherited from
  the fixture design; #16's sealed-evaluator requirement applies to decision-grade runs,
  not this screen). Prompts do not reference `hidden_check.py`; runner receipts bind the
  tree so post-verify tampering stales acceptance.
- gpt-oss-120b cached-input price is unverified (conservatively billed as full input in
  `pricing.yaml`); cost gaps involving arm C are lower bounds on C's advantage.
- Timeouts count as failures and remain in the denominator (intention-to-treat).
