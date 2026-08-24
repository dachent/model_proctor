## Stable key
TOOL-002

## Outcome
Per-task metered cost is attached to each cascade task's evidence entry automatically: after each dispatch, match the child session's wire.jsonl usage records to the task and record cost_usd by model in cascade-state.json. Leader cost tracked separately per goal.

## Why
Today per-task attribution is manual reconstruction from wire logs. The 2026-08-14 measurement (leader 96% of spend; delegated slice $9.44 as-routed vs ~$17.85 K3 counterfactual) was assembled by hand and is not repeatable.

## In scope
- Wire-log → task matching by child_session_id (delegate envelope already captures it).
- meter.py integration; costs visible in `cascade.py status`.

## Blocked by
- #1 (TOOL-001)

## Final evidence and handoff
(to be filled at close)
