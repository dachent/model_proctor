# PREREG-model-mode — E-mech and E-ship (TOOL-031 Phase 3)

Date: 2026-09-10. Sealed by commit before any E-mech or E-ship runs
(#96 Phase 3, per the amended FINDINGS requirements 6 — the two-experiment
acceptance that replaced the underpowered "within noise" batch).

## E-mech — mechanism attribution (task.json in-tree vs relocated)

**Question.** Is the ~+28k-token/run M4-early proctored-arm overhead caused
by `task.json` sitting in the worker's tree (pilot writes it in-workspace;
the plain driver's prompt file is outside)?

**Design.** The EXISTING proctored path (runner init/dispatch/verify/
accept/record via pilot), one variable: arm M-in writes `task.json` into the
workspace as pilot does today; arm M-out relocates it to the state root
(the runner accepts a task path anywhere — `--task <path>`). Same 10 v3
cases, 3 reps per case per arm = 60 runs, fresh fixture per rep, one
dispatch per case (`max_dispatches=1`), **interleaved at the rep level**
(arm alternates per rep, cancelling time-of-day and session variance — the
F4 lesson). Wire-metered, corrected pricing.

**Primary metric.** `usage_records` per run (the one-extra-turn signature
identified in QC B1: ~6.37 vs 5.30 records/run in F4). Secondary: tokens
per run, `api_cost_usd` per run, wall time.

**Decision rules (frozen).**

1. **Mechanism confirmed** if M-in's mean usage-records/run exceeds
   M-out's by ≥ 1.0 record (the observed F4 delta) AND a two-sided
   Welch t-test on per-run records gives p < 0.05.
2. **Mechanism excluded** if the arms are within 0.3 records/run of each
   other AND p > 0.30 — the task.json hypothesis dies, and the F4 overhead
   is attributed to between-session variance, reopening the
   interleaved-recheck question for E-ship interpretation.
3. In between: **inconclusive** — report, do not claim attribution; the
   conversion still ships on E-ship alone (requirement 3's claim is
   "designed out", not "proven caused").
4. Tokens/cost/wall are reported as secondary; they do not override the
   records metric (records is the mechanism's direct signature and the
   cheapest to power).

**Budget.** ~$0.60 (60 proctored-path flash runs at ~$0.0093/run observed
in F4/phase3-rotation). Ceiling $1.50.

## E-ship — shipping equivalence gate (model-mode vs plain)

**Question.** Does the converted dispatch path (`delegate.py --model`) cost
the same as plain delegate dispatch, within a margin that matters?

**Design.** Arm S-model: `delegate.py --model fireworks/glm-5p3-flash`
(the new Phase-1 path, live-catalog-validated, template-synthesized).
Arm S-plain: `delegate.py --agent glm-flash-worker` (the roster path, the
N arm of PREREG-plain-vs-proctored). Same 10 v3 cases, fresh fixture per
rep, prompt via external `--task-file` in BOTH arms, same timeout. 6 reps
per case per arm = 120 runs, **interleaved at the rep level** (alternating
arms per rep). Graded identically: pristine fixture checks copied over the
workspace before check.py + hidden_check.py. Wire-metered, corrected
pricing. (6 reps — not 3 — to give the equivalence test power without
doubling the budget to #96's ceiling.)

**Primary metric.** `api_cost_usd` per run (A13 semantics: malformed usage
meters as unknown and is excluded from the test with its count reported).

**Decision rules (frozen).**

1. **TOST equivalence** on per-run cost with margin δ = $0.0012 (the F4
   effect size: 16% of the plain arm's mean). Equivalence is declared iff
   the 90% CI of (S-model − S-plain) mean difference lies entirely within
   (−δ, +δ). This is the standard two-one-sided-tests procedure at
   α = 0.05.
2. **Fail** if the CI breaches the margin in either direction: the
   converted path does NOT ship until the breach is explained and fixed.
3. Quality is monitored, not gating: both arms' hidden-pass rates are
   reported; a quality gap > 3/60 hidden-passes reopens the question but
   does not fail cost equivalence (cost is the conversion's risk).
4. Interleaving is mandatory: any run that cannot be paired (crash,
   provider failure) stays in the denominator for cost (intention-to-treat)
   but is excluded from the paired analysis with its count reported.
5. **Ship** iff rule 1 passes. E-mech's outcome does not gate shipping
   (requirement 3's claim is "designed out"; attribution is E-mech's job).

**Budget.** ~$1.10 (120 flash-dispatch runs at ~$0.008–0.009 each).
Ceiling $2.50.

## Integrity notes

- E-ship arms differ ONLY in dispatch mode (model-template vs roster
  entry): same transport, same envelope contract, same metering, same
  prompt text, same timeout. The N arm of PREREG-plain-vs-proctored is the
  direct ancestor of S-plain (same code path, `--agent` mode).
- E-mech modifies pilot to write `task.json` outside the workspace for
  M-out (a `--task-outside-ws` flag, default off = current behavior);
  the flag's diff is part of the sealed commit.
- Screen limitation as ever (#16): hidden checks grade the final tree.
- No E-mech or E-ship row exists at seal time. Total new spend ceiling
  across both: **$4.00**.
