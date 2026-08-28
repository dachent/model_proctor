# PREREG — verifier error on existing result rows (EVAL-004)

Frozen before the numbers are interpreted, per the repo's practice in
`PREREG-v2.md` and `PREREG-v3.md`. The measurement itself is free — it reads
committed rows and spawns no model — so the discipline here is entirely about
not reading a conclusion into an underpowered sample.

## Question

How often does the acceptance verifier pass work that the hidden check rejects?

## Why it matters

README Phase 3 makes this constraint load-bearing for the design:

> **Cascading only beats baseline when verifier/judge error ≤ 0.1**; at 0.2
> performance may deteriorate rapidly. (RouterBench, arXiv:2403.12031)

The threshold has never been evaluated against this repo's own verifiers.

## Estimand — fixed before running

**Primary: conditional.** `P(accept | NOT hidden)` = accepted-and-hidden-failed
÷ all rows where the hidden check failed. This is the misclassification rate the
RouterBench threshold governs.

**Secondary, reported but not gating: joint.** `P(accept AND NOT hidden)` ÷ all
rows. Recorded only because it is the figure a reader would naively compute, and
because the gap between the two must stay visible.

Naming a rate without its denominator is out of bounds. On `results.jsonl` the
two read 0.25 and 1.00 on identical rows.

## Corpora — analysed separately, never pooled

| File | acceptance field | what it measures |
|---|---|---|
| `results.jsonl` | `acceptance_pass` | bare `kimi.exe` via `run_eval.py` — no runner, no seal, no receipt |
| `pilot-*.jsonl`, `phase2-*.jsonl`, `phase3-*.jsonl` | `accepted` | the runner's tree-bound acceptance |

Pooling is prohibited: these are different estimands. Neither is a measurement
of deployed verifier error on real tasks.

## Power rule — the part that binds

By the rule of three, zero events in *n* trials puts the 95% upper bound at
about 3/*n*. Resolving a 0.1 threshold therefore needs roughly **30 rows in the
conditional denominator** — that is, 30 rows where the hidden check failed.

**Any file with fewer than 30 hidden failures is declared underpowered, and its
conditional rate is reported as a raw count only.** No verdict — favourable or
unfavourable — may be drawn from it. This rule is stated here precisely because
the row counts were visible in advance and it would otherwise be trivial to
select the reading that suits.

## Decision rules

1. **Underpowered (< 30 hidden failures in every corpus).** Conclusion is
   *"this corpus cannot resolve verifier error"*. That is a real and reportable
   finding: it means the v1/v2/v3 screens say less about acceptance quality than
   their headline pass rates imply, and any future claim resting on the
   RouterBench threshold needs a corpus built for it. **No code changes are
   justified by this outcome.**
2. **Powered and conditional ≤ 0.1.** The cited constraint is satisfied on that
   corpus, for that corpus's estimand only. No generalisation to production.
3. **Powered and conditional > 0.1.** The design is outside the envelope it
   cites. Escalate: a corpus-specific verifier-strengthening issue, and the
   README claim gets qualified.
4. **Any corpus where `accepted=True` for every row.** Report the acceptance
   check as non-discriminating **on that corpus** and say so explicitly, rather
   than reporting a rate that is really a property of the fixtures.

## Kill criterion

If this measurement produces only underpowered results and no follow-up corpus
is built, `evals/verifier_error.py` stays as a reporting tool and **no
architectural conclusion is drawn from it**. Do not retain the framing that
verifier error has been "measured".

## Known limits, stated before results

- `hidden_check.py` is not ground truth. Both prior PREREGs record that hidden
  checks run inside the agent-writable workspace (sealed by `pilot.py`, but
  still a second fallible instrument). Estimating verifier error against an
  imperfect oracle bounds how far any number here can be trusted.
- v2/v3 fixtures are **admitted** on the rule that a naive solution must pass
  `check.py` and fail `hidden_check.py` (`PREREG-v3.md`). Their check/hidden gap
  is an authoring criterion, so measuring it partly measures fixture design.
- The RouterBench threshold is cited in README Phase 3 in the context of the
  **static cascade**, which the README labels a frozen research artifact. The
  live path is described as "no economic routing". Whether the constraint
  transfers to the shipped design is itself an open question, not an assumption
  this measurement settles.
- n is small everywhere and every corpus is a single-operator screen.
