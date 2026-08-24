## Stable key
TOOL-005

## Outcome
Image-bearing tasks route correctly through the cascade: the plan schema carries a modality flag, the controller's vision capability filter (spec §9.1 v3.1) rejects assignment of image tasks to text-only workers, and the worker lineup includes a vision-capable metered option (e.g. Fireworks Qwen/Kimi vision variants) with an escalation rule for image complexity.

## Why
Owner directive 2026-08-14: the cascade must NOT be text-only. The v3.1 spec added the vision capability filter; the roster and escalation rule are not implemented.

## In scope
- Plan-schema modality field + controller validation.
- agents.json vision-capable worker entries.
- Skill text: when images escalate and to what.

## Blocked by
None

## Final evidence and handoff
(to be filled at close)
