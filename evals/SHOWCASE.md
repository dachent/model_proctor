# Showcase Scenarios — Pre-registered Predictions (written BEFORE any run)

Purpose: decide whether the routing layer has real value on the task classes where theory
says it should win. These are value-demonstration scenarios, separate from the tune/holdout
benchmark sets. Recorded 2026-07-30 before fixtures were built or run.

## Configs under test

- **A**: plain Kimi Code (K3 alone, no routing skill)
- **C**: full routing with loosened gate (mechanical/bounded work with deterministic
  verification goes to ds-flash even when multi-file; K3 validates via test suite)
- **D**: ds-v4-pro-start (every non-trivial task delegated to DeepSeek V4 Pro as default
  worker through the wrapper; K3 only adjudicates: runs tests, inspects diff, one resume on
  mechanical failure, GLM native on conceptual failure; trivial tasks stay K3-direct)

## Scenario S1 — bulk_explore (exploration at scale)

Fixture: synthetic order-processing system, ~50 Python modules, ~250-350KB of code.
Task: trace a refund request end-to-end; write the ordered list of modules + functions to
ANSWER.md. Acceptance: check script greps ANSWER.md for the correct ordered facts.
Hidden test: additional facts not mentioned in the task prompt.

Prediction: C and D beat A on wall-clock by >= 2x AND on est-tokens by >= 3x, with all
configs passing acceptance (result quality equal).
Rationale: bulk file reading is carried by a cheap 1M-context high-throughput model instead
of K3's expensive context; the leader consumes a short report.

## Scenario S2 — bulk_migrate (mechanical multi-file edit with deterministic acceptance)

Fixture: ~30-module Python project using a deprecated pattern throughout (deprecated
logging accessor and a moved utility module); full pytest suite.
Task: migrate every module to the new pattern; all tests must pass.
Acceptance: pytest exits 0. Hidden test: checks no deprecated pattern remains anywhere,
including files the task prompt does not enumerate.

Prediction: C and D beat A on est-tokens by >= 2x and are no slower on wall-clock, with all
configs passing acceptance.
Rationale: the bulk edit is mechanical and cheaply verifiable — exactly the band where a
cheap fast worker plus deterministic acceptance should dominate on cost.

## Falsification condition

If A ties or beats BOTH of C and D on BOTH axes (wall-clock and est-tokens) in BOTH
scenarios, the routing layer has no measurable value on its strongest ground:
keep `delegate` + eval harness, retire the routing skill, and say so in the scorecard.

## Runs

2 cases x 3 configs x 3 reps = 18 runs, --resume enabled. Blinded scorecard via report.py.
Adoption judgment uses median per case; this is a value screen, not a statistical proof.
