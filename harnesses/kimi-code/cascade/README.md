# cascade — deterministic controller for the static model cascade

`cascade.py` is the enforcement plane for the design in
[`policy/STATIC_CASCADE_SPEC.md`](../policy/STATIC_CASCADE_SPEC.md) (v3, §4/§5/§7/§8).
The GLM orchestrator (a Kimi session) is a *decision* component; **all** state
transitions, caps, verifier-immutability checks, and delegate invocations go
through this CLI. Prompts don't enforce; code enforces.

Stdlib only, Python 3.10, Windows-native. All state writes are atomic
(temp file + `os.replace`). All subprocesses are `shell=False` argument arrays.
Git is mutated only by the explicit `commit-green` and `rollback` subcommands
(`git stash create` checkpoints create a dangling commit object and mutate
nothing); every other git invocation is read-only. Git-dependent features
(checkpoint diffs, commit-green, rollback) require a repo with at least one
commit — a HEAD-less repo is treated as non-git.

## Commands

State lives in a cascade directory: `<workspace>/.orchestrator/` by default
(`cascade-state.json`, `cascade-log.jsonl`, `packets/`). Override with
`CASCADE_STATE_DIR`.

```bash
# Validate the planner's task list, freeze verifier hashes, record a git
# checkpoint ref, write cascade-state.json. --threat-model is owner-set and
# immutable for the goal.
python cascade/cascade.py init --workspace W --plan-file plan.json \
    --threat-model single-operator|adversarial-local|hostile-input

# Legality-checked dispatch of one task. Prints the delegate JSON envelope
# on success; prints {"allowed": false, "reason": ...} and exits 3 on refusal.
# The task's evidence entry gains child_exit_code, files_changed (tracked diff
# vs the checkpoint ref PLUS an untracked-file inventory — or the string
# "unavailable_not_a_git_repo"), and run_dir log archival (see below).
python cascade/cascade.py dispatch --workspace W --task t1 [--reason R] \
    [--timeout 1800] [--advisor codex|claude] [--escalate]

# Apply a QC verdict. --findings is an inline JSON array or a path to one;
# required for reject (each finding needs at least severity/location/claim).
# Accepting despite blocker/major findings requires --dismiss-reason; the
# findings are then stored verbatim in the goal's dismissed_findings.
python cascade/cascade.py record-qc --workspace W --task t1 \
    --verdict accept|accept-with-minor-fixes|reject [--findings F] \
    [--root-cause M] [--dismiss-reason R]

# Re-hash the frozen verifier set (ANY change = automatic reject + escalate),
# then run the deterministic verifier command ourselves, shell=False,
# cwd=workspace, output bounded to a 64 KiB tail.
python cascade/cascade.py verify --workspace W --task t1 [--timeout 300]

# One JSON line: tasks done/total, counters, cost vs ceiling, open-task rungs,
# threat model, dismissed findings.
python cascade/cascade.py status --workspace W

# Replace the task list. Requires --confirm and >=2 tasks failed with the same
# root-cause marker. Counts against the planner+replan cap (2/goal).
python cascade/cascade.py replan --workspace W --plan-file new.json \
    --root-cause M --reason "..." --confirm

# Commit the task's scope paths (git add + commit, standard message). Legal
# only after a controller-run verify pass for that task — worker-reported
# green never counts. Refuses (exit 3) otherwise, when the workspace is not a
# git repo, or when the scope has nothing staged.
python cascade/cascade.py commit-green --workspace W --task t1

# Scoped rollback: git checkout <checkpoint_ref> -- <scope paths>. Legal only
# when the task's last dispatch is in a terminal envelope state
# (completed/failed/timeout) — a pending native K3 dispatch refuses.
# Untracked worker files are left in place and reported.
python cascade/cascade.py rollback --workspace W --task t1

# Bounded bootstrap packet (hard 8KB cap) for session resumes: goal, threat
# model, per-task status/rung/attempts, counters, decisions log, dismissed
# findings, evidence dir pointer. Deterministic from state.
python cascade/cascade.py handoff --workspace W

# Append to the goal's append-only decisions log.
python cascade/cascade.py record-decision --workspace W \
    --decision "..." --rationale "..." [--rejected "..."] --source user|leader
```

Exit codes: `0` ok · `1` verification failed/rejected · `2` invalid input ·
`3` legality refusal (stdout carries `{"allowed": false, ...}`) ·
`4` infrastructure error (delegate transport/envelope failure, attempt NOT
counted). For `dispatch`, exit 0 means the delegate envelope is authoritative —
`completed` and `failed` both exit 0, exactly like the wrapper.

## What IS enforced

- **Plan schema** (`init`/`replan`): required fields, types, and enums checked
  by a stdlib validator (`validate_plan`) mirroring `cascade-schema.json`.
  Unknown executors rejected; executor→profile mapping is `flash→ds-flash-worker`,
  `pro→ds-pro-worker`, `k27→k27-worker`, `k3→native`; delegate profiles must
  exist in the agents config. `pro` requires a non-empty `pro_reason`. Verifier
  files must exist at init and stay inside the workspace.
- **Escalation ladder** (exactly): `assigned(flash|pro|k27) → k3 → advisor →
  post-advisor k3 → stop`. Per-rung budget = `max_attempts` (1–2, default 2)
  and resets on escalation; advisor rung = 1 call; post-advisor K3 = 1 retry
  (normal) / 2 (high). First cap hit stops the task (`status: failed`).
- **Caps**: executor attempts 5 normal / 6 high per task (2+2+1, +1 high);
  QC reviews 2 normal / 3 high; planner+replan 2 per goal; advisor calls are
  quota-tracked (`counters.advisor_calls`), not capped.
- **Vision capability routing** (spec §9.1 v3.1): `VISION_CAPABLE = {flash: False,
  pro: False, k27: True, k3: True}` — an explicit constant in cascade.py.
  A task is vision-bearing when its plan entry carries `"vision": true` OR its
  scope contains image files (`.png .jpg .jpeg .gif .bmp .webp .tiff`,
  case-insensitive; scope directories are walked when they exist).
  Vision-bearing tasks REQUIRE a vision-capable executor (k27|k3): `init`
  rejects flash/pro assignments, and `dispatch` re-checks defensively (scope
  contents may have changed since init) with `vision_capability_violation`.
  This is a capability filter orthogonal to the difficulty ladder — an image
  task is impossible for a text-only model at any retry depth, so it is never
  an escalation matter.
- **Cost ceiling**: `dispatch` refuses once `cost_used_usd >= cost_ceiling_usd`
  and warns on stderr at ≥ 50% (`cost_warning_usd`). `cost_used_usd` is
  maintained externally by `evals/meter.py` — the controller only enforces the
  gate; it never estimates cost itself (log cost/token fields are null).
- **Verifier immutability**: sha256 of every `verifier_set` file is frozen at
  init/replan; `verify` re-hashes before doing anything else. ANY change or
  deletion → reject + `force_escalate`, never accept. Verifier commands are
  re-run by the controller; worker-reported test output is never trusted.
  The same failure text across two different rungs sets
  `verifier_defect_suspect` (spec §5 escape hatch).
- **QC structure**: reject verdicts require findings with at least
  severity/location/claim; accept verdicts are refused while blocker/major
  findings are open — unless dismissed with `--dismiss-reason`, which stores
  each blocking finding verbatim in the goal's `dismissed_findings` (surfaced
  by `status` and `handoff`; the final report must include them verbatim).
- **Evidence hardening**: every counted delegate dispatch records
  `child_exit_code`, `files_changed` (tracked diff vs the pre-dispatch
  checkpoint ref plus a `git ls-files --others` untracked inventory — the
  checkpoint is blind to new files), and archives the run_dir's
  `stdout.log`/`stderr.log` into `.orchestrator/evidence/<task_id>-<n>/` with
  sha256 hashes in state. Non-git workspaces record
  `"unavailable_not_a_git_repo"` instead of a diff.
- **Threat model**: `--threat-model` is required at `init`, stored in state,
  and immutable for the goal (replan preserves it).
- **Decisions log**: `record-decision` appends timestamped
  decision/rationale/rejected-alternatives/source entries; `handoff` emits a
  deterministic ≤8KB bootstrap packet (goal, tasks, counters, decisions,
  evidence pointer) for session resumes.
- **Atomicity**: `cascade-state.json` is written via temp-file + `os.replace`;
  a crash leaves either the old or the new file, never a partial one.
- **Infrastructure vs task failures** (§8): envelope parse errors, launch
  failures, `invalid`/`interrupted` are not counted as attempts; transient
  `internal_error` gets exactly one retry with backoff
  (`CASCADE_RETRY_BACKOFF`, default 2 s); `timeout` IS counted (the worker may
  have made partial progress — inspect via the per-task `checkpoint_ref`).
- **Resume plumbing**: within-rung retries reuse the child session via
  delegate `--resume-from <child_session_id>` (spec §9.7, measured 5× cheaper
  input); escalation to a different model is always a fresh dispatch.

## What is NOT enforced (and who owns it)

- **Cost metering**: cascade.py writes `cost_usd: null` and null token fields.
  Real metering is `evals/meter.py` parsing wire.jsonl; it is also responsible
  for writing `cost_used_usd` back into `cascade-state.json`.
- **Filesystem containment**: the only enforced boundary is the delegate
  wrapper's `allowed_workspace_roots`. Scope adherence is post-hoc diff
  inspection by the orchestrator (spec §3/§8). cascade.py checks that plan
  *paths* stay inside the workspace, but does not sandbox workers.
- **Quality judgment**: QC verdicts, acceptance, and the decision to escalate
  early (`--escalate`) are orchestrator decisions; the controller only rejects
  *illegal* transitions and over-cap invocations.
- **Native K3 rungs**: the controller cannot spawn a native subagent. At the
  `k3` / `k3_post_advisor` rungs it counts the attempt, writes the packet, and
  prints a `{"status": "native_dispatch", ...}` envelope instructing the
  orchestrator to run K3 fresh (`model="secondary"`).
- **Verifier command splitting**: commands are split with POSIX `shlex`.
  On Windows use forward slashes and quote paths with spaces.

## Environment overrides

| Variable | Default | Purpose |
|---|---|---|
| `CASCADE_DELEGATE` | `../delegate/delegate.py` | Delegate wrapper to invoke. Tests point this at `tests/fake_delegate.py`. |
| `CASCADE_AGENTS_CONFIG` | `DELEGATE_CONFIG`, else `../delegate/agents.json` | Agents config used to validate executor→profile mapping. |
| `CASCADE_STATE_DIR` | `<workspace>/.orchestrator` | Cascade directory. |
| `CASCADE_RETRY_BACKOFF` | `2` | Seconds before the single transient-error retry. |

## Delegate interface note

cascade.py is written against the delegate resume interface: envelope
field `child_session_id` and the `--resume-from <session_id>` flag (landed in
`delegate/delegate.py`; spec §9.7). The live `delegate/agents.json` carries the
spec §9.2 profile names (`ds-pro-worker`, `k27-worker`), matching the
executor→profile mapping enforced at init. The test suite nonetheless runs
entirely against `tests/fake_delegate.py` (canned envelopes) — no real CLIs.

## Tests

```bash
python -m unittest discover -s cascade/tests -v
```

Covers: legal/illegal dispatch matrix, full escalation ladder at normal (cap 5)
and high (cap 6) criticality, cost-ceiling stop and 50% warning, verifier
tamper detection (fixture modifies a frozen test file between init and verify),
schema-validation rejections, atomic state write under a simulated crash,
envelope parse failures, transient retry, timeout counting, QC caps and
findings validation, blocker/major dismissal with verbatim storage, replan
preconditions and cap, log line format, status line, threat-model requirement
and immutability, decisions log, dispatch evidence hardening (exit code,
files_changed incl. untracked inventory, run_dir log archival with sha256),
commit-green gating, rollback legality and scoped restore, handoff packet
content/determinism/cap. Git-backed tests build a throwaway repo inside the
fixture workspace (skipped when git is unavailable). Fixture workspaces live
under `cascade/tests/tmp/` (gitignored, cleaned up per test).
