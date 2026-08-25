# model_proctor

Deterministic control plane for coding agents on Kimi Code CLI: task-owning workers
dispatched through a lean Windows-native `delegate` wrapper, leader-executed verification
with tree-bound acceptance receipts, wire-metered cost accounting, and a pre-registered
evaluation harness. (Formerly kimi_router; the static-cascade routing design is preserved
as a frozen research artifact — see Status.)

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
- `runner/` — the MVP-001 thin control plane (`runner.py`): frozen task-start lane
  selection, delegate-wrapper dispatch, leader-side verification with tree-bound
  receipts and a config-surface manifest, stagnation switching, append-only task
  records. State, receipts, and sealed verifier payloads live OUTSIDE the workspace
  (`<ws_parent>/.runner-state/<ws>-<hash>/`, override with `--state-dir`; TOOL-014).
  Smoke suite in `runner/tests/` (S1–S7 + git-root cases). Designs out the
  frozen cascade's trust-boundary defects (#17–#20) rather than patching them.
- `policy/delegation-policy.md` — the K3 orchestration policy (routing decision procedure, fire
  rules, task packet schema, budgets, stopping rules, final acceptance gate). The production skill
  installed to `%USERPROFILE%\.kimi-code\skills\static-cascade\` is derived from this hierarchy.
- `evals/` — benchmark harness: `cases.yaml` (42 cases: 20 v1 tune/holdout + 2 showcase +
  10 v2 + 10 v3 quality sets),
  `fixtures/` (deterministic project generators), `run_eval.py` (A/B/C runner), `report.py`
  (scorecard), `skills/{A,B,C}/` (config isolation skill dirs for `--skills-dir`), and the tracked
  evidence set (`results.jsonl`, `results-metered.jsonl`, `blinding-key.json`, `scorecard.md`).
- `.orchestrator/tmp/` — session scratch. Ephemeral by doctrine: deleted 2026-08-25 (DOC-002,
  issue #25); anything worth keeping must be committed or filed as an issue first.

## Commands

- Wrapper tests: `python -m unittest discover -s delegate/tests -v`
- Cascade tests: `python -m unittest discover -s cascade/tests -v`
- Extractor tests: `python -m unittest discover -s scripts/tests -v`
- Runner smoke suite (MVP-001): `python -m unittest discover -s runner/tests -v`
- Delegate a task: `python delegate/delegate.py --agent <name> --workspace <path> --task "<text>"`
- Cascade extras: `python cascade/cascade.py commit-green|rollback --workspace <w> --task <id>`,
  `handoff --workspace <w>`, `record-decision --workspace <w> --decision ... --rationale ... --source user|leader`
- Extract a session log: `python scripts/extract_log.py <wire.jsonl...> --out <dir>`
- Rebuild watch: `python evals/meter.py --rebuild-watch <hours>`
- Eval self-test: `python evals/run_eval.py --self-test`
- Eval scorecard: `python evals/report.py`

## Conventions

- **Durability doctrine (owner directive 2026-08-25):** everything on `C:\` is ephemeral. Only
  content stored on GitHub is durable or referenceable; anything else must be
  cloneable/reproducible/derivable from the repo. Session scratch, run envelopes, and local configs
  either get committed, get filed into issues, or are treated as disposable.
- Python 3.10, standard library only everywhere.
- No git mutations without explicit user confirmation. Repository creation on this workstation must
  use `New-CentralGitRepo.ps1` (centralized Git policy); never raw `git init`.
- Durable installs follow workstation policy: tools to `C:\Tools\model-proctor\`, skills to
  `%USERPROFILE%\.kimi-code\skills\`, each with explicit user confirmation.
- Eval fixture runs live outside cloud-synced folders (default: `C:\Dev\bootstrap-state\model-proctor\evals\runs\`;
  historical metered rows keep their original `kimi-router` paths — the evidence record is immutable).
- When delegating coding work from this project, follow `policy/delegation-policy.md`.
- All work on this tool is managed via the GitHub Issues backlog at
  `dachent/model_proctor` (owner directive 2026-08-14; repo renamed from
  `dachent/robot_lockstep_ballast` → `kimi_router` → `model_proctor` 2026-08-25, old URLs redirect): every unit of work is an
  issue with
  a `[AREA-NNN]` key, `priority:now|next|parked` labels, a `## Blocked by` section, and a non-empty
  `## Final evidence and handoff` before closing. Parked items carry an explicit activation trigger.

## Status

Research artifact frozen at commit 6095695 (2026-08-13). Governance decision: dachent/model_proctor #16. Installed skill: `static-cascade`.
