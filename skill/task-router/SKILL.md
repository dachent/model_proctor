---
name: task-router
description: vNext task-owning worker policy (MVP-001, issue #27) — classify a task into a frozen lane (Flash bounded / GLM substantial / K3 marathon), dispatch through the installed runner (C:/Tools/model-proctor/runner.py), verify with tree-bound receipts, switch on stagnation rather than fixed rungs. Use for substantial coding tasks. Do NOT use for trivial tasks (known location, one edit-test cycle) — execute those directly. Supersedes nothing yet; static-cascade remains the frozen reference. Requires the model-proctor install (scripts/install.py).
---

# Task Router Policy (vNext MVP)

You are the leader. The deterministic control plane is the installed runner,
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
   interpreter), and `budget`.
2. **Lane**: `python C:/Tools/model-proctor/runner.py lane --task task.json` — record the decision. Override
   only by setting `lane` in the task file, and note why in the task record.
3. **Init**: `python C:/Tools/model-proctor/runner.py init --workspace <w> --task task.json`. Refusal
   (`workspace_is_not_repo_root`) is final — fix the workspace, never bypass.
4. **Dispatch**: `python C:/Tools/model-proctor/runner.py dispatch --workspace <w> --task task.json`. The
   worker owns the engineering trajectory in its own session; you own state and acceptance.
5. **Verify**: `python C:/Tools/model-proctor/runner.py verify --workspace <w> --task task.json`. The runner
   rejects verification if any verification-affecting file (conftest.py, pytest.ini,
   pyproject.toml, *.pth, ...) appeared or changed since init, then runs the verifier itself.
   Never trust worker-reported results.
6. **Accept**: `python C:/Tools/model-proctor/runner.py accept --workspace <w> --task task.json`. A green
   receipt stales automatically on any tree mutation — re-verify after every change.
7. **Record**: `python C:/Tools/model-proctor/runner.py record --workspace <w> --task task.json [--wire
   <wire.jsonl> --pricing evals/pricing.yaml]` — appends the append-only task record.

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
