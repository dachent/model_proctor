---
name: model-proctor
description: Task-owning worker policy with deterministic acceptance (model_proctor) — classify a task into a frozen lane (Flash bounded / GLM substantial / K3 marathon), dispatch through the installed runner (C:/Tools/model-proctor/runner.py), verify with tree-bound receipts, switch on stagnation rather than fixed rungs. Use for substantial coding tasks. Do NOT use for trivial tasks (known location, one edit-test cycle) — execute those directly. Supersedes nothing yet; static-cascade remains the frozen reference. Requires the model-proctor install (scripts/install.py).
---

# Model Proctor Policy

The proctor assigns the exam, watches the clock, and grades it objectively — the model never
marks its own work. You are the leader. The deterministic control plane is the installed runner,
`C:/Tools/model-proctor/runner.py` (repo copy: `runner/runner.py`; delegate transport and the
agent roster live alongside it at `C:/Tools/model-proctor/`). Prompts do not
enforce; the runner enforces. Deterministic evidence outranks every model, including you.
Governance: issues #16 (measurement-first) and #26 (vNext hypothesis); this skill implements
only the MVP slice (#27). STOP — one fixed worker wins — remains a legitimate outcome.

## Entry gate

Use the runner only for substantial tasks (unfamiliar code, multi-file, or estimated
≥ ~3 turns). Trivial work: do it yourself. Never spend a worker dispatch on a task you can
complete in one edit-test cycle.

## Lane table (frozen for the experiment — no per-run relabeling)

| Task shape | Lane |
|---|---|
| localized + bounded + known location + objective acceptance | `flash` |
| multi-module / unfamiliar repo / substantial refactor | `glm` |
| open-ended exploration, research engineering, marathon | `k3` |
| no bounded signature, substantial | `glm` (default) |

Decomposition itself is the hard problem? Optionally consult K3 for a task breakdown first —
that is a planning consult, not a mandatory planner tax.

## Flow (one task-scoped worker session per task)

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
   record. Use the **installed** pricing table, not a relative `evals/` path: the relative form
   resolves only when you happen to be sitting in the repo root, and a missing pricing file drops
   cost accounting silently rather than failing loudly.

The receipt records the tree signature, the dispatch count it was written at (`dispatch_seq`), and
the `verifier_argv` that produced it — so a green receipt states *what* was verified, not merely
that something was.

It also carries `baseline_tree` (the tree had not moved since init when this verifier ran) and
`verifier_nondiscriminating` (it *passed* on that unmodified tree). The second is a warning to you,
not a refusal: a verifier that goes green before the worker has touched anything will go green
whatever the worker does, so acceptance carries no information. Some tasks legitimately pass at
init — "add a test that…" — so read it, don't obey it.

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

## Session discipline

One persistent worker session per task, closed at acceptance. Externalize verified state to
files continuously; do not accumulate completed task detail in your own context. If you are
asked to resume a dead trajectory, restart from the evidence packet instead.
