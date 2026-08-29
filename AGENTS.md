# model_proctor

Deterministic control plane for coding agents on Kimi Code CLI: task-owning workers
dispatched through a lean Windows-native `delegate` wrapper, leader-executed verification
with tree-bound acceptance receipts, wire-metered cost accounting, and a pre-registered
evaluation harness. (Formerly kimi_router; the static-cascade routing design is preserved
as a frozen research artifact — see Status.)

## Layout

Harness-specific code lives under `harnesses/<harness>/`; everything cross-harness
(`evals/`, `scripts/`, `policy/`, `docs/`, `backlog/`) stays at the repo root. A file
under `harnesses/` reaches repo-level assets only through an explicit `REPO_ROOT`,
never through the harness root.

- `harnesses/kimi-code/delegate/` — the wrapper (`delegate.py`), live config (`agents.json`), annotated example
  (`agents.example.json`), tests (`tests/test_delegate.py`), docs (`README.md`).
- `harnesses/kimi-code/cascade/` — the deterministic static-cascade controller (`cascade.py`), **frozen research
  artifact**, with plan schema (`cascade-schema.json`), tests (`tests/test_cascade.py` + fixture delegate fake), docs
  (`README.md`). Owns cascade-state.json transitions, caps, legal escalation transitions,
  verifier-immutability checks, dispatch evidence hardening (files_changed + run_dir log
  archival), `commit-green`/`rollback` git gates, `handoff`/`record-decision` session
  continuity, and the vision capability filter (spec §9.1 v3.1). It is retained for
  provenance and regression research, not as production authority.
- `scripts/` — `extract_log.py` (deterministic wire.jsonl fact extractor + coverage
  manifest; verifier-class, hash-frozen per goal), tests in `scripts/tests/`,
  `install.py`.
- `harnesses/kimi-code/runner/` — the MVP-001 thin control plane (`runner.py`): frozen task-start lane
  selection, delegate-wrapper dispatch, leader-side verification with tree-bound
  receipts and a config-surface manifest, stagnation switching, append-only task
  records. State, receipts, and sealed verifier payloads live OUTSIDE the workspace
  (`<ws_parent>/.runner-state/<ws>-<hash>/`, override with `--state-dir`, which now
  refuses a path inside the workspace; TOOL-014/017). Designs out the frozen
  cascade's trust-boundary defects (#17–#20) rather than patching them.
  Acceptance gate (TOOL-015/016/018): a receipt flagged `tamper_detected` is refused
  rather than annotated; receipts carry `dispatch_seq` and `verifier_argv`, so one
  written before a later dispatch no longer authorises acceptance and a green receipt
  states *what* was verified; the verification contract is pinned at init and verify
  refuses on divergence; `-m` module shadowing is rejected (cmd_verify runs with
  `cwd=ws`, so the workspace is `sys.path[0]`); the git tree signature hashes content,
  not just `git status` letters. `init` refuses to re-baseline an initialized workspace
  without `--reinit`. Production-runner tasks (TOOL-019) are barred from `flash` absent
  an explicit `lane`, and require fresh `preflight_receipts`. `harnesses/kimi-code/runner/pilot.py` drives
  the loop against real workers and appends an evidence row.
  Tests in `harnesses/kimi-code/runner/tests/`: S1–S7 + git-root cases, `test_production_guard.py`,
  `test_acceptance_gate.py`, `test_tree_signature.py`, `test_verifier_integrity.py`,
  `test_state_boundary.py`.
  The boundary is **tamper-evident against a non-adversarial worker**, not sealed
  against a hostile one — residuals tracked in #40.
- `policy/delegation-policy.md` — superseded Phase-2 dynamic-routing research policy.
  It is retained for provenance only; it is not production authority and no installed
  skill is derived from it.
- `evals/` — benchmark harness: `cases.yaml` (42 cases: 20 v1 tune/holdout + 2 showcase +
  10 v2 + 10 v3 quality sets),
  `fixtures/` (deterministic project generators), `run_eval.py` (A/B/C runner), `report.py`
  (scorecard), `skills/{A,B,C}/` (config isolation skill dirs for `--skills-dir`), and the tracked
  evidence set (`results.jsonl`, `results-metered.jsonl`, `blinding-key.json`, `scorecard.md`).
- `.orchestrator/tmp/` — session scratch. Ephemeral by doctrine: deleted 2026-08-25 (DOC-002,
  issue #25); anything worth keeping must be committed or filed as an issue first.

## Commands

- Wrapper tests: `python -m unittest discover -s harnesses/kimi-code/delegate/tests -v`
- Cascade tests: `python -m unittest discover -s harnesses/kimi-code/cascade/tests -v`
- Extractor tests: `python -m unittest discover -s scripts/tests -v`
- Runner smoke suite (MVP-001): `python -m unittest discover -s harnesses/kimi-code/runner/tests -v`
- Contract parity (core vs Kimi, exhaustive lane table): `python -m unittest discover -s core/tests -v`
- ZCode harness: `python -m unittest discover -s harnesses/zcode/tests -v`
- Delegate a task: `python harnesses/kimi-code/delegate/delegate.py --agent <name> --workspace <path> --task "<text>"`
- Cascade extras: `python harnesses/kimi-code/cascade/cascade.py commit-green|rollback --workspace <w> --task <id>`,
  `handoff --workspace <w>`, `record-decision --workspace <w> --decision ... --rationale ... --source user|leader`
- Extract a session log: `python scripts/extract_log.py <wire.jsonl...> --out <dir>`
- Rebuild watch: `python evals/meter.py --rebuild-watch <hours>`
- Eval self-test: `python evals/run_eval.py --self-test`
- Eval scorecard: `python evals/report.py`
- Verifier error over committed rows: `python evals/verifier_error.py [--json]`
  (free — no model runs; read `evals/PREREG-verifier-error.md` for the decision
  rule before interpreting the output, and never quote a rate without its
  denominator)
- Real-dispatch pilot: `python harnesses/kimi-code/runner/pilot.py --cases <id> --lane <lane> --max-dispatches 1`
  (spends real tokens)

- `core/decisions.py` — the shared decision core: lane table, failure fingerprints,
  stagnation thresholds, scope matching, verification-affecting set. Every harness
  calls it rather than reimplementing. Contract v1, spec in
  `policy/HARNESS_CONTRACT.md`; `core/tests/test_kimi_parity.py` enumerates the full
  lane truth table against `runner.lane_for`.
- `harnesses/zcode/` — the ZCode harness. ZCode owns subagent dispatch, so the proctor
  gates it in front of the `Agent` tool rather than owning it. Native shims contain no
  policy: every constant arrives in `lane.json` from the core.

## Conventions

- **Durability doctrine (owner directive 2026-08-25):** everything on `C:\` is ephemeral. Only
  content stored on GitHub is durable or referenceable; anything else must be
  cloneable/reproducible/derivable from the repo. Session scratch, run envelopes, and local configs
  either get committed, get filed into issues, or are treated as disposable.
- Python 3.10, standard library only for control-plane logic. Harness *adapters*
  may use the harness's native runtime where measured startup cost requires it,
  and must contain no policy (see `policy/HARNESS_CONTRACT.md`; ZCode's shims are
  Node because ZCode spawns a hook per tool call — Python measured 4.7-6.4s cold,
  0.67-1.16s warm, against Node's 0.25-0.38s).
- No git mutations without explicit user confirmation. Repository creation on this workstation must
  use `New-CentralGitRepo.ps1` (centralized Git policy); never raw `git init`.
- Durable installs follow workstation policy: tools to `C:\Tools\model-proctor\`, skills to
  `%USERPROFILE%\.kimi-code\skills\`, each with explicit user confirmation.
- Eval fixture runs live outside cloud-synced folders (default: `C:\Dev\bootstrap-state\model-proctor\evals\runs\`;
  historical metered rows keep their original `kimi-router` paths — the evidence record is immutable).
- Live orchestration authority is `harnesses/kimi-code/runner/` plus
  `harnesses/kimi-code/skill/model-proctor/SKILL.md`. Do not use
  `policy/delegation-policy.md` as live routing policy; it is a superseded research artifact.
- All work on this tool is managed via the GitHub Issues backlog at
  `dachent/model_proctor` (owner directive 2026-08-14; repo renamed from
  `dachent/robot_lockstep_ballast` → `kimi_router` → `model_proctor` 2026-08-25, old URLs redirect): every unit of work is an
  issue with
  a `[AREA-NNN]` key, `priority:now|next|parked` labels, a `## Blocked by` section, and a non-empty
  `## Final evidence and handoff` before closing. Parked items carry an explicit activation trigger.

## Status

Research artifact frozen at commit 6095695 (2026-08-13). Governance decision: dachent/model_proctor #16. Installed skill: `model-proctor` (live policy). `static-cascade` remains in-repo only as a frozen research artifact.
