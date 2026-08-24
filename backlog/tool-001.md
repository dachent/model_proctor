## Stable key
TOOL-001

## Outcome
All production dispatch (real project work, not just cascade tests) flows through `cascade.py` — init / plan / dispatch / record-qc / verify / commit-green — instead of ad-hoc direct `delegate.py` calls. The controller's caps, legal-transition enforcement, evidence archival, and QC counters apply to real work by default.

## Why
During the 2026-08-13/14 production dogfood (quant_engine takeover), real work was driven via `delegate.py` directly; the controller's protections (QC caps, evidence archival, threat-model field) did not apply. Per-task cost attribution had to be reconstructed manually.

## In scope
- Dogfood run: one real governed goal executed end-to-end via cascade.py.
- Gaps found during the dogfood fixed or filed.

## Blocked by
None

## Final evidence and handoff
(to be filled at close: run record, state file, cost attribution diff vs manual baseline)
