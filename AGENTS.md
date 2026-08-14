# kimi_router

Multi-model routing harness for Kimi Code CLI: Kimi K3 as lead orchestrator, GLM-5.2 native
secondary subagents, and a lean Windows-native `delegate` subprocess wrapper for external CLI
workers (fast scouts, cheap workers, independent reviewers, read-only advisors).

## Layout

- `delegate/` — the wrapper (`delegate.py`), live config (`agents.json`), annotated example
  (`agents.example.json`), tests (`tests/test_delegate.py`), docs (`README.md`).
- `cascade/` — the deterministic static-cascade controller (`cascade.py`), plan schema
  (`cascade-schema.json`), tests (`tests/test_cascade.py` + fixture delegate fake), docs
  (`README.md`). Owns cascade-state.json transitions, caps, legal escalation transitions,
  verifier-immutability checks, dispatch evidence hardening (files_changed + run_dir log
  archival), `commit-green`/`rollback` git gates, `handoff`/`record-decision` session
  continuity, and the vision capability filter (spec §9.1 v3.1).
- `scripts/` — `extract_log.py` (deterministic wire.jsonl fact extractor + coverage
  manifest; verifier-class, hash-frozen per goal), tests in `scripts/tests/`,
  `install.py`.
- `policy/delegation-policy.md` — the K3 orchestration policy (routing decision procedure, fire
  rules, task packet schema, budgets, stopping rules, final acceptance gate). The production skill
  installed to `%USERPROFILE%\.kimi-code\skills\multi-model-routing\` is generated from this.
- `evals/` — benchmark harness: `cases.yaml` (20 pre-registered cases, tune/holdout split),
  `fixtures/` (deterministic project generators), `run_eval.py` (A/B/C runner), `report.py`
  (scorecard), `skills/{A,B,C}/` (config isolation skill dirs for `--skills-dir`).
- `.orchestrator/tmp/` — session scratch (canaries, review packets, diagnostics). Not durable.

## Commands

- Wrapper tests: `python -m unittest discover -s delegate/tests -v`
- Cascade tests: `python -m unittest discover -s cascade/tests -v`
- Extractor tests: `python -m unittest discover -s scripts/tests -v`
- Delegate a task: `python delegate/delegate.py --agent <name> --workspace <path> --task "<text>"`
- Cascade extras: `python cascade/cascade.py commit-green|rollback --workspace <w> --task <id>`,
  `handoff --workspace <w>`, `record-decision --workspace <w> --decision ... --rationale ... --source user|leader`
- Extract a session log: `python scripts/extract_log.py <wire.jsonl...> --out <dir>`
- Rebuild watch: `python evals/meter.py --rebuild-watch <hours>`
- Eval self-test: `python evals/run_eval.py --self-test`
- Eval scorecard: `python evals/report.py`

## Conventions

- Python 3.10, standard library only everywhere.
- No git mutations without explicit user confirmation. Repository creation on this workstation must
  use `New-CentralGitRepo.ps1` (centralized Git policy); never raw `git init`.
- Durable installs follow workstation policy: tools to `C:\Tools\kimi-router\`, skills to
  `%USERPROFILE%\.kimi-code\skills\`, each with explicit user confirmation.
- Eval fixture runs live outside OneDrive at `C:\Dev\bootstrap-state\kimi-router\evals\runs\`.
- When delegating coding work from this project, follow `policy/delegation-policy.md`.
- All work on this tool is managed via the GitHub Issues backlog at
  `dachent/robot_lockstep_ballast` (owner directive 2026-08-14): every unit of work is an issue with
  a `[AREA-NNN]` key, `priority:now|next|parked` labels, a `## Blocked by` section, and a non-empty
  `## Final evidence and handoff` before closing. Parked items carry an explicit activation trigger.
