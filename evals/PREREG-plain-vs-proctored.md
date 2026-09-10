# PREREG-plain-vs-proctored — does the proctor's dispatch path cost anything?

Date: 2026-09-09. Sealed by commit before any plain-arm runs (#83 M4, pulled
forward per the 2026-09-09 session direction: run the economic kill-switch
early, not last). Companion evidence: PREREG-v3-rotation (sealed `0d38db9`).

## Why

Twice-confirmed STOP says routing adds nothing and the fixed arm is ~$0.01
per hidden-pass. The proctor's remaining unproven claim is economic: that
task-owning dispatch through the control plane beats the plain alternative
at equal quality. This comparison measures exactly that, at matched
protocol, before more machinery is built. Driver: `evals/plain_arm.py`
(sealed with this document).

## Arms (matched: same model, same transport, same prompts, same fixtures)

| Arm | Dispatch | Verification |
|---|---|---|
| **P (proctored)** | runner `init`/`dispatch`/`verify`/`accept` via pilot — the sealed PREREG-v3-rotation rows (`evals/phase3-rotation-2026-09-09.jsonl`, arm `glm-5p3-flash`, 30 runs) | runner verify (sealed check restored on tamper) + post-accept hidden run |
| **N (plain)** | `delegate.py --agent glm-flash-worker --task-file <prompt> --timeout 600` directly — the same transport, NO control plane (no lane/init/seal/receipts/budget/journal) | none during the run; the scorer grades after: pristine `check.py` + `hidden_check.py` copied over the workspace (equalizing the proctored arm's sealed-restore) |

Same 10 v3 cases (`q11_*` … `q20_*`), 3 reps per case per arm, fresh fixture
per rep, one attempt per case (no rescue either side — the proctored rows
ran `max_dispatches=1`), same prompt text, same effective timeout (600s).

## Metrics

Per arm: accepted (`check.py` rc 0), hidden-pass, all-in worker
`api_cost_usd` (wire-metered, corrected pricing, A13 unknown-not-zero),
wall time, cost per hidden-passing run. Grading is identical: both arms'
final trees are graded against the PRISTINE fixture checks.

## Decision rules (frozen)

1. **Economic non-inferiority:** the proctor is non-inferior if P's cost per
   hidden-passing run is within +10% of N's AND quality (hidden count) is
   within 3/30.
2. **Interpretation, precommitted:**
   - **tie** (both rules pass) → the dispatch path is economically neutral on
     bounded tasks; the proctor's value claim reduces to the trust boundary
     and accounting. #83 continues as trust repairs; no economic adoption
     claim is made or needed.
   - **N materially cheaper or better** → the dispatch path itself needs
     rework before any M4 adoption claim; investigate before building M2.
   - **P materially cheaper or better** → first measured economic evidence
     FOR the proctor; proceed to the routed-vs-direct-strong comparison.
3. Wall-time differences are reported but do not rule; the metric is dollars
   per correct task.
4. Provider failures/timeouts count as failures on both sides
   (intention-to-treat) and stay in the denominator.

## Budget

Estimate ~$0.30 (the proctored flash arm metered $0.279 for 30 runs). Hard
ceiling **$5** — abort and report beyond it.

## Integrity notes

- Plain-arm asymmetries, stated honestly: no seal — the worker could read
  the hidden check during its run; the pristine-copy grading after the run
  equalizes the GRADE, not the worker's visibility. No budget enforcement
  (single attempt matches the proctored arm's cap). Delegate's native
  envelope only — no journal, no stall detection. That absence is the
  variable under test.
- The proctored arm's rows were collected under PREREG-v3-rotation on the
  same corpus/protocol/driver and are reused as arm P, sealed, unmodified.
- Screen limitation as ever: hidden checks grade the final tree; not
  decision-grade (#16).

## Amendment (2026-09-09, before the first valid run)

The first execution attempt crashed after one completed dispatch
(q11 rep1, ~cent-level spend, no row appended): the driver had been written
against #94's `scan_usage_records`, which is not on main. The driver is now
self-contained on main's stable `sum_usage_records` and enforces the
operative guarantee itself — zero surviving usage records means UNKNOWN and
`api_cost_usd: null`, never $0. Full malformed-record discrimination
remains the runner-side A13 fix (#94) and applies to these rows once
merged. No plain-arm evidence rows existed at amendment time; arm P is
untouched.
