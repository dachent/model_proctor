---
name: model-proctor
description: Task-owning worker policy with deterministic acceptance and evidence-driven escalation (model_proctor) — run substantial work through the gated path (runner.py init/dispatch/verify/accept; flash-first with escalation to glm-5p3 then kimi-k3 on recorded stagnation), or start a one-shot subagent with any model from the live harness catalog (delegate.py --model). Use for substantial coding tasks. Do NOT use for trivial tasks — execute those directly. Requires the model-proctor install (scripts/install.py).
---

# Model Proctor Policy

The proctor assigns the exam, watches the clock, and grades it objectively — the model never
marks its own work. You are the leader. Two surfaces, one doctrine (#96, the 2026-09-09/10
findings): **the gated path** for substantial work (`C:/Tools/model-proctor/runner.py`
init/dispatch/verify/accept — flash-first, escalating to glm-5p3 then kimi-k3 on recorded
stagnation), and **thin dispatch** for consults and one-shots
(`C:/Tools/model-proctor/delegate.py --model <id>`). Prompts do not enforce; the tools
enforce. Deterministic evidence outranks every model, including you. Governance: #16, #91,
#96.

## The gated path (substantial work — the default)

Runner state, receipts, and sealed verifier payloads live OUTSIDE the workspace
(`.runner-state/` sibling) — the worker cannot rewrite its own evidence. At verify time the
runner restores any tampered verification input from the sealed copy and flags it on the receipt.

1. **Write the task file**: `task.json` with `task_id`, `prompt`, `features`, `scope`
   (non-empty), `verifier.argv` (an argv ARRAY — never a shell string; use `{python}` for the
   interpreter), and `budget`. Tasks that drive a known production runner also require
   `preflight_receipts` — see **Production tasks** below.
2. **Lane**: `python C:/Tools/model-proctor/runner.py lane --task task.json` — record the decision. Override
   only by setting `lane` in the task file, and note why in the task record.
3. **Init**: `python C:/Tools/model-proctor/runner.py init --workspace <w> --task task.json`. Refusal
   (`workspace_is_not_repo_root`) is final — fix the workspace, never bypass. Init pins the
   verification contract (`verifier`, `seal`) into external state; the task file sits inside
   the worker-writable tree, so from here on the pinned copy is authoritative.
4. **Dispatch**: `python C:/Tools/model-proctor/runner.py dispatch --workspace <w> --task task.json`. The
   worker owns the engineering trajectory in its own session; you own state and acceptance.
5. **Verify**: `python C:/Tools/model-proctor/runner.py verify --workspace <w> --task task.json`. The runner
   rejects verification if any verification-affecting file (conftest.py, pytest.ini,
   pyproject.toml, *.pth, ...) appeared or changed since init, if the task file's verifier
   diverges from the pin (`verifier_changed_since_init`), or if a workspace file shadows a
   module the verifier imports via `-m` (`module_shadow_detected` — the workspace is
   `sys.path[0]`, so a dropped `unittest.py` would otherwise swallow the run). Then it runs
   the verifier itself. Never trust worker-reported results.
6. **Accept**: `python C:/Tools/model-proctor/runner.py accept --workspace <w> --task task.json`. A green
   receipt stales automatically on any tree mutation — re-verify after every change.
   Accept also refuses when the receipt carries `tamper_detected` (a sealed verification
   input was altered and the runner restored it) and when any dispatch happened after the
   receipt was written. Both clear by re-running `verify` — never by re-running `accept`.
7. **Record**: `python C:/Tools/model-proctor/runner.py record --workspace <w> --task task.json [--wire
   <wire.jsonl> --pricing C:/Tools/model-proctor/pricing.yaml]` — appends the append-only task
   record. Use the **installed** pricing table, not a relative `evals/` path. Malformed or
   missing usage meters as **unknown, never $0** (`usage_unknown` on the row).

The receipt records the tree signature, the dispatch count it was written at (`dispatch_seq`), and
the `verifier_argv` that produced it — so a green receipt states *what* was verified, not merely
that something was. It also carries `baseline_tree` and `verifier_nondiscriminating`; when
`dispatch_seq == 0` and `verifier_nondiscriminating == true`, `accept` refuses by default —
`--allow-zero-dispatch` is the reviewed, counted exception.

## Lane table (live routing policy — flash-first with evidence-driven escalation)

Bounded/spec-complete tasks start on `flash` (glm-5p3-flash, the cheapest measured arm:
$0.0096/hidden-pass, #91). The escalation ladder is **evidence-driven, not start-time
guesswork** — a lane changes only on recorded stagnation (see Failure classes below).

| Task shape | Lane |
|---|---|
| localized + bounded + known location + objective acceptance | `flash` |
| multi-module / unfamiliar repo / substantial refactor | `glm` |
| open-ended exploration, research engineering, marathon | `k3` |
| no bounded signature, substantial | `glm` (default) |

Why flash-first is right even though benchmarks showed "one fixed worker wins": the STOP
rulings were measured on ~30-second bounded tasks where every tier was quality-tied. On
long agentic work, per-unit failure compounds; cheap-first-plus-gates beats both
fixed-strong (always — the price ratio is ~16x) and fixed-cheap (beyond the break-even
task size; see README "The routing break-even"). Escalation is what earns its keep there.

Decomposition itself is the hard problem? Optionally consult K3 for a task breakdown first —
that is a planning consult, not a mandatory planner tax.

## Start a subagent with a model (consults and one-shots)

```bash
python C:/Tools/model-proctor/catalog.py                  # what's live + what it costs
python C:/Tools/model-proctor/delegate.py \
    --model fireworks/glm-5p3-flash \
    --workspace <ws> \
    --task "<task text>"          # or --task-file <path> (preferred: no
                                  # control-plane files in the worker's tree)
```

- The model id is validated against **kimi's live config** at dispatch time;
  rotated ids refuse loudly with same-family alternatives listed. Config
  presence ≠ serving — a listed id can still 404 at the provider; that
  surfaces as a provider failure in the envelope.
- Read-only by default; `--write` is explicit (and model-mode only).
- `--resume-from <session_id>` continues a child session (the template's
  resume_args).
- Every child carries `PROCTOR_CHILD=1` (injected); a nested delegate refuses
  `--model`/`--write` spawns — no unmanaged nesting.
- Production-pattern tasks (`run_week.ps1`, `src.run_all`, ...) are REFUSED
  in model-mode; they belong on the gated runner path above.

## Production tasks

If the prompt, scope, or verifier names a known production entrypoint (`run_week.ps1`,
`src.run_all`, `src.run_weekly`, `run_readiness_doctor`, `morning_battery`), the runner
treats the task as ops-class:

- **The flash lane is refused** unless you set an explicit `lane` in the task file. `init`
  and `dispatch` now agree on this; an explicit override is a reviewed decision, so record
  why in the task record.
- **`preflight_receipts` is mandatory** — a non-empty array of paths to logs or reports the
  orchestrator's own probe (doctor / battery / dry-run) actually produced. Missing files
  refuse (`preflight_receipt_required`); receipts older than 24h refuse
  (`preflight_receipt_stale`, override with `budget.max_preflight_age_s`). Discovery is
  what probes are for, not what dispatch budgets are for.

This is a **known-entrypoint denylist over your own task text**, not a general ops-class
detector: a prompt that never names one of those entrypoints will not trip it. Declaring
features honestly is still your job.

## Failure classes and switching (not a fixed ladder)

- **Provider/tool failure** (timeout, internal_error) → switch provider/harness lane, not a
  smarter model.
- **Execution stagnation** (identical normalized failure fingerprint, 3 in a row) → lateral
  switch: flash→glm, glm→k3, k3→glm. The new worker gets a compact evidence packet —
  objective, acceptance criteria, current diff, verified test output, fingerprints, explicit
  switch reason — never the failed model's full rationale.
- **Localized defect after broad success** → same-worker targeted repair.
- Budgets are hard caps (`max_dispatches`, `max_stagnant`, `timeout_s`). A refusal is final
  until state changes legally.

## Stopping rules (TOOL-011, #12 — owner directive 2026-08-16)

1. Max **two adversarial QC rounds per change**; a third round is an owner call.
   Test-strength-only findings (no production defect) are record-and-ship — note
   them on the issue, do not loop.
2. Re-run only the **narrowest failing stage**; rebuild upstream stages only when
   an upstream input actually changed.
3. Gates (full suite + linters) re-run only **after a code change**.
4. Budgets are declared at dispatch and escalate on breach instead of continuing
   (`max_dispatches`, `max_stagnant`, `timeout_s` above; #83 M1 extends them to
   per-attempt reservations).

## Session discipline

One persistent worker session per task, closed at acceptance. Externalize verified state to
files continuously; do not accumulate completed task detail in your own context. If you are
asked to resume a dead trajectory, restart from the evidence packet instead.

### Resuming after a gap (mandated first step — #73/A2)

The FIRST action on any resume — new morning, reopened machine, post-crash — is
`python C:/Tools/model-proctor/runner.py status --workspace <w> --task task.json`:

- `stall_suspected: true` (silence beyond `max(2x timeout_s, 1h)`) and any
  `orphaned_dispatch_ids` are **stop-and-investigate** signals. The 2026-08-28 overnight
  stall went unnoticed for 8.9 hours because nobody ran anything like this; do not
  reproduce that with better instrumentation installed.
- Orphans are advisory. Investigate, then clear with
  `python C:/Tools/model-proctor/runner.py journal --workspace <w> --ack <dispatch_id>`
  so they stop re-reporting.
- `last_receipt` with `dispatch_seq: 0` + `verifier_nondiscriminating: true` carries no worker
  evidence; `accept` refuses it unless `--allow-zero-dispatch` is supplied. That override is
  counted and should be treated as a reviewed exception.
