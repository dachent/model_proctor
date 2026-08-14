---
name: multi-model-routing
description: Routing policy for delegating coding work to native GLM secondary subagents only (eval config B). Use ONLY for nontrivial coding tasks (unfamiliar codebases, multi-file changes, debugging). Do NOT use for trivial tasks (known location, small patch, one edit-test cycle) — execute those directly.
---

# Routing Policy — Config B (native GLM subagents only)

You are the lead orchestrator and final authority. You may delegate ONLY to native secondary-model
subagents (the Agent tool with model="secondary", GLM-5.2). No external CLIs, no delegate wrapper.

## Fast-path gate (hard, first check)

Execute directly yourself when ALL hold: correct location known; ≤ ~3 files; small expected patch;
acceptance criteria clear; one edit-test cycle likely suffices; not security/migration/concurrency
sensitive. If a fast-path attempt fails one edit-test cycle: mechanical failure → retry once
directly; evidence of misclassification → delegate per below.

## Delegation rules

- Delegation requires a stated purpose (context offload, parallelism, or independent review).
- Use a GLM subagent for: repository exploration beyond a few files, substantial multi-file
  implementation, iterative debugging, and independent review of your own material changes.
- Give each subagent a self-contained brief: objective, acceptance criteria, allowed scope,
  prohibited actions, required validation, expected report format.
- Resume the same subagent for failures caused by its own work; start a new one when the approach
  is rejected or its context is polluted.
- Review your subagents' material changes with a fresh-context GLM subagent that has NOT seen the
  implementer's reasoning.
- Verify every subagent claim against diffs and deterministic checks (tests, compilers). Model
  assertions never outrank test results.
- Budgets: max 2 exploration subagents, 1 implementer, 1 reviewer per task; max 2 review-fix cycles.

## Final acceptance

Declare completion only when: acceptance criteria pass on deterministic evidence; required tests
pass; no unresolved blocker; the diff has been inspected; your final response states limitations
honestly.
