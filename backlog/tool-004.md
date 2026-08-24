## Stable key
TOOL-004

## Outcome
Eval suite v2: fixtures that actually discriminate on quality (the v1 suite passed all configs at 1.00 acceptance+hidden — it could not detect quality differences), real wire-log metering as the PRIMARY cost metric (the v1 est_tokens proxy was broken; tokens_reported was null throughout), and a re-run of the pre-registered adoption rule: cascade config must beat K3-alone on holdout success at lower cost-per-success without regressing simple-case wall-clock.

## Why
The v1 benchmark rejected dynamic routing on cost (+16%/+37% real metering) but could not say anything about quality. The standing claim "static cascade beats K3-alone on cost at fixed quality" currently rests on the spec's cost MODEL and production anecdotes, not a discriminating benchmark.

## In scope
- Harder fixtures (multi-constraint tasks with graded rubrics, not just exit codes).
- run_eval.py wired to results-metered.jsonl extraction by default.
- New pre-registered decision rule BEFORE runs.

## Blocked by
None

## Final evidence and handoff
(to be filled at close)
