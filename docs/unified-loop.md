# The unified session controller loop (v1)

Status: seed implemented 2026-10-30 on `feat/escalation-wirewatch`. Design
record: `review/loop-design.md`; binding corrections: `review/disprove-loop.md`.
This document is the design *with those corrections applied* — where the two
disagree, this document and the code are the corrected version, and each place
they disagree is marked.

One cron task per session, one prompt, five jobs in a fixed order, every fire
bounded. Escalation is Job 0, not a second watcher.

```
harnesses/kimi-code/loop/loop_spec.py    the spec + byte-stable prompt renderer
harnesses/kimi-code/loop/template.md     the versioned prompt body (v1)
harnesses/kimi-code/loop/loopctl.py      bind / verify / job0 / advancement / clear / notify
harnesses/kimi-code/loop/tests/          the suite (50 tests, part of the CI matrix)
harnesses/kimi-code/hooks/proctor_advancement_gate.ps1   the host PreToolUse gate
```

## 1. The fire, and what "non-blocking" really means

A fire is one agent turn that runs the five jobs in order and returns. Budgets:
Job 0 escalation 30 s, Job 1 delta 30 s, Job 2 verdict + deliver 15 s, Job 3
advance 15 s, Job 4 reconcile 15 s — 105 s of job budget inside a 180 s fire,
with a cadence of 10 minutes. `loopctl bind` refuses a spec whose cadence is
under 3× the fire budget.

**Correction (disproof 2).** The design said the loop "never blocks". It does
block, bounded: the fire runs inside the leader session, so the owner's typed
steering queues behind at most one fire budget (worst case 180 s). The prompt
says so in those words. Anything long — a full suite, a large wire evaluation —
is launched detached and consumed by a later fire; nothing long runs inline.

**Correction (disproof 5).** A cron fire is a model turn with no hard timeout.
The budget is a prompt-honored convention, not a scheduler guarantee.

## 2. The job registry

| # | Job | Budget | Emits | Suppresses |
|---|---|---|---|---|
| 0 | ESCALATE | 30 s | `ESCALATED` `NOMINAL` `UNKNOWN` `LOCKED` | Job 3 |
| 1 | DELTA | 30 s | `NOMINAL` `UNKNOWN` | — |
| 2 | VERDICT + DELIVER | 15 s | `NOMINAL` `UNKNOWN` | — |
| 3 | ADVANCE | 15 s | `NOMINAL` `UNKNOWN` | — |
| 4 | RECONCILE | 15 s | `NOMINAL` `DRIFT` | — |

`ESCALATED`/`NOMINAL`/`UNKNOWN`/`LOCKED`/`DRIFT` is the entire vocabulary; a
suppressed Job 3 emits `NOMINAL suppressed=<reason>`. Every branch of every job
ends in one of these strings — string drift between prompt and emitters is the
defect class that produced the two silent no-ops in session bbad08a0.

## 3. Job 0 — escalation

`loopctl job0` imports `evaluator.evaluate_paths` / `evaluator.write_outputs`
from `harnesses/kimi-code/wirewatch/` — never a second parser, never a second
set of triggers. It resolves this session's wires from the spec's globs
(`*/<session_id>/agents/*/wire.jsonl` under the Kimi sessions root), writes
`<state>/wirewatch/{escalations.jsonl,summary.json}`, records the run in
`<state>/job0/last_run.json`, and prints one line:

```
LOOP:v1 JOB0 ESCALATED stops=1 lane_switch=3 flag=0 wires=6 unmeasurable=T10 new=4 key=8c3cb0a64eac
```

Exit codes: `0` NOMINAL, `1` ESCALATED (stop severity), `3` UNKNOWN, `4`
LOCKED, `2` usage error. `loopctl verify` returns `0` NOMINAL and `5` DRIFT. A
missing wire is `UNKNOWN(reason=no_wires)`, never an
empty pass. The evaluator's `check_out_dir` still refuses an `--out` inside the
workspace or a session dir (TOOL-014); inside the loop that surfaces as
`UNKNOWN(out_dir_refused)`, which suppresses advancement.

**Wire-set size.** Above 5 MB or 8 wires, `job0` returns
`UNKNOWN(job0_detached_required)` in about a second and prints the exact
detached command. The prompt tells the fire to launch it detached and report
UNKNOWN this fire. `loopctl` does not spawn the process itself: a subprocess
spawn inside a fire is the pattern the design moved out of the loop.

**Re-verify at delivery.** Every fire re-derives findings from the live wire;
only dedupe state (`delivered.jsonl`) persists. A late or coalesced fire cannot
deliver a finding the wire has outgrown, and cannot miss one that aged in during
a blind window.

**Thresholds** are bound, not re-derived: `loop_spec` holds a
`wire_triggers.Thresholds` instance (default `wire_triggers.DEFAULTS`, which
imports `STAGNATION_THRESHOLD`/`DEFAULT_MAX_STAGNANT` from `core/decisions.py`).
A session spec cannot carry thresholds at all — `load_spec` refuses the key.

## 4. Advancement suppression, and where it is enforced

**Correction (disproof 3, the stale-read bypass).** The design suppressed Job 3
only on positive stops. Job 0 goes detached for large wire sets, so a fire would
have read *last* fire's `summary.json` and spawned a worker while an escalation
evaluation was in flight. `advancement_allowed` therefore refuses when this
fire's Job 0 did not land, before it looks at any stop count:

| Reason | Meaning |
|---|---|
| `job0_never_ran` | no `job0/last_run.json`: nothing to trust |
| `job0_stale:age=…>max=…` | the record is older than one cadence — detached, crashed, or the fire skipped Job 0 |
| `job0_unknown:<reason>` | this fire's Job 0 was UNKNOWN / LOCKED / DRIFT |
| `stops_unresolved:<n>:<keys>` | stop-severity keys with no owner clear on record |
| `ok[:stops_cleared=<n>]` | allowed |

`job0_max_age_ms` is capped at one cadence at bind time: a record that cannot
have come from this fire is stale by construction, and stale is not clear.

**Correction (disproof 5, the sharpest miss).** Instruction is not enforcement.
The prompt's Job 3 check is a readable reason; it stops nothing. Enforcement is
a host `PreToolUse` hook, `proctor_advancement_gate.ps1`, registered with
`matcher = "Agent"`: it enumerates bound loop state dirs, self-filters on the
payload's `session_id`, asks `loopctl advancement --state-dir <dir>`, and exits
`2` (block, stderr becomes the model-visible reason) when the answer is not
allowed. With no bound loop it is inert.

The hook deviates from the host's fail-open hook convention **on purpose**:
when a loop *is* bound to the session and the decision cannot be read, it
blocks (`gate_unreadable`). A fail-open spawn gate is exactly the failure this
hook exists to remove. A defect inside the hook itself still fails open, with a
warning, because that is not a loop decision.

**Deviation from the design, stated.** The design's invariant 13 also suppressed
advancement while a *lane_switch* finding was undelivered. v1 does not: a
lane_switch is delivered (loudly, once, deduped) but does not gate spawning.
The brief's rule is narrower on purpose — an undelivered lane_switch is a
delivery defect, not evidence that a worker must not be spawned.

## 5. The prompt as code

`template.md` is the versioned body (header `LOOP:v1`, job registry first, then
per-job budgets and outcome strings, the invariants split by enforcement class,
the line cap, and the ledger). `render_prompt(spec)` substitutes session slots
and is byte-stable:

- line endings are normalized to LF on the way in and on the way out, so a
  CRLF checkout cannot manufacture a different sha256 (and therefore a false
  DRIFT);
- no timestamp, host name or dict ordering reaches the output;
- an unknown `{{SLOT}}` raises instead of rendering a literal.

`loopctl bind` validates the spec, renders, writes `<state>/installed.json`
(`prompt_sha256`, `template_sha256`, `spec_sha256`, `cron_expr`, `session_id`,
`job0_max_age_ms`, the full spec for re-rendering, and the drift-guard scope
string), and prints the exact `CronCreate` arguments. Only the agent can create
the slot; bind never touches the scheduler.

**Correction (disproof 4). The drift guard is accident detection.** Job 4's
`loopctl verify` re-renders and compares against `installed.json`. Both sides
are agent-writable, and `CronList` truncates the stored prompt to ~200 bytes, so
a hand-edited live cron prompt passes forever. It catches "someone edited the
template after bind"; it is not a proof, and `installed.json` carries that
sentence so no future reader has to rediscover it. The only version proof a
transcript offers is the `LOOP:v1` header, and that needs a reader.

## 6. Delivery

Three layers, only one of which is durable:

1. **Hook heartbeat (best-effort).** `[[hooks]] event = "SessionHeartbeat"` runs
   `loopctl notify` every 60 s with no model in the path. It self-filters on
   `session_id`, compares stop keys against `delivered.jsonl`, raises a Windows
   toast through `powershell.exe` (with a documented `msg.exe` fallback that is
   absent on Windows Home and needs a console session), and appends
   `owner-inbox.jsonl`. Re-toasting the same unresolved key is throttled to once
   per 30 minutes — re-toasting every 60 s is fatigue, not delivery.
   **This layer is zero-tested on this host and is labelled so in the prompt, in
   `installed.json` and in the code.** `SessionHeartbeat` also dies with the CLI
   process, which is exactly the unattended-crash case.
2. **The fire's reply.** The verdict line plus a few human lines, deduped on the
   identity key `sha256(trigger, wire, evidence)[:12]`. `NotifyUser` remains a
   convenience layer: agent-invoked, experimental, suppressible.
3. **On disk — the durable guarantee.** `escalations.jsonl`, `summary.json`,
   `delivered.jsonl`, `owner-inbox.jsonl`, `job0/last_run.json`, all outside
   every proctored workspace. Delivery is repeated on the next fire until the
   owner runs `loopctl clear --state-dir <dir> --id <key> --reason "<why>"`, which
   is counted (`clear_count`) and refuses a key that was never delivered.

## 7. Invariants: enforced vs honored

**Correction (disproof 5).** Roughly half the original fourteen are aspirations
the prompt asks for. The prompt now says which is which.

Tool-enforced — a program catches the violation:

| # | Invariant | Mechanism |
|---|---|---|
| I1 | exactly one cron slot | Job 4 asserts it via `CronList`; bind renders before delete |
| I3 | cadence > 3× fire budget | `validate_spec` refuses the spec |
| I6 | dedupe on the identity key | `loopctl notify` owns the comparison |
| I13 | advancement suppression | `advancement_allowed` + the `PreToolUse` hook (exit 2) |
| I14 | no self-suppression | `evaluator.check_out_dir` (TOOL-014) |

Convention — prompt-honored, nothing blocks a violation: I2 (bounded fire — no
hard timeout exists), I5/I12 (exact strings — review is the only check), I7
(transition-firing), I9 (self-lock is checked by the turn it restrains), I10
(owner-cancel marker), I11 (memory on disk).

## 8. What v1 does not prevent

Stated plainly, because the design's headline overclaimed and the disproof was
right about that.

- **It is an observer plus a spawn gate, not a hard gate on all work.** The hook
  gates `Agent` spawns. It does not gate a worker launched by `Bash`
  (`delegate.py`, a background shell, the tower's own internal work), and it
  does not gate work the leader does itself in the same turn. A session that
  never spawns `Agent` is not gated at all.
- **The gate only exists once the hook is registered.** Registration edits
  `~/.kimi-code/config.toml`, which is in the G5 protected set. That is a
  decision-seat step with a non-Kimi review; this work documents the snippet in
  the hook header and here, and does not perform it. Until it is registered, all
  suppression is the prompt asking nicely.
- **A cron fire is not interruptible and has no hard timeout.** A pathological
  fire can run long; the budget is convention.
- **The drift guard cannot verify the live prompt.** See §5.
- **The toast is not a guarantee.** Untested here, fail-open, dead when the CLI
  is not running, auto-dismissed on an unattended desktop.
- **The heartbeat's mid-turn behaviour is unknown.** If `SessionHeartbeat` does
  not fire during an open turn, the hook covers only inter-turn gaps and the
  blind window is bounded by the next fire's re-verify.
- **Compaction erases context.** The ledger lives in the prompt and on disk; a
  hand-edited prompt is not drift-detected if `installed.json` is edited too.
- **The lock is age-based, not pid-based.** A crashed fire holds the lock until
  its TTL (2× fire budget + 120 s grace) — the cost of a host-independent,
  testable decision.
- **`--out` protection is scoped to the workspace and session dir** the
  evaluator is told about. A state dir placed somewhere worker-writable by a bad
  spec is refused at bind only if the workspace is passed to `bind`.
- **A spec can be bound without the hook or the cron.** `bind` writes state and
  prints arguments; nothing verifies a human (or agent) actually created the
  slot. Job 4's `CronList` assert is the detection.

## 9. Operating it

```bash
# 1. bind (validates, renders, writes installed.json, prints CronCreate args)
python harnesses/kimi-code/loop/loopctl.py bind --spec session-loop.json \
    --workspace <proctored workspace>

# 2. create exactly one cron with the printed arguments (delete+recreate in one
#    step when replacing: render first, then delete the old slot)

# 3. per fire
python harnesses/kimi-code/loop/loopctl.py job0 --state-dir <state>
python harnesses/kimi-code/loop/loopctl.py advancement --state-dir <state> --json
python harnesses/kimi-code/loop/loopctl.py verify --state-dir <state>

# 4. owner-only, counted
python harnesses/kimi-code/loop/loopctl.py clear --state-dir <state> \
    --id <key> --reason "reviewed: fixed in <commit>"

# 5. the hook entrypoint (stdin = the Kimi hook payload)
... | python harnesses/kimi-code/loop/loopctl.py notify --state-root <loop root>
```

Spec file (strict schema; unknown keys and any `thresholds` key are refused):

```json
{
  "session_id": "session_…",
  "state_dir": "C:/Dev/bootstrap-state/model-proctor/loop/<slug>",
  "cadence_minutes": 10,
  "fire_budget_s": 180,
  "job0_max_age_ms": 600000,
  "kimi_sessions_root": "C:/Users/<user>/.kimi-code/sessions",
  "journal_path": "",
  "ledger": ["next item", "after that"]
}
```

Hook registration (decision-seat step, not performed here):

```toml
[[hooks]]
event = "PreToolUse"
matcher = "Agent"
command = 'pwsh -NoProfile -NonInteractive -File "C:/Tools/model-proctor/loop/proctor_advancement_gate.ps1"'
```

## 10. Follow-ups

- `scripts/install.py` does not yet ship `loop/` (template, spec, loopctl, hook)
  to `C:\Tools\model-proctor\`. Until it does, the hook's default path resolution
  falls back to the repo layout.
- Whether `SessionHeartbeat` fires mid-turn is untested; a live toast test on
  this host would move the delivery layer from "best-effort" to "verified".
- Whether crons survive restart and compaction in all cases: Job 4 detects,
  `bind` repairs.
- Job 1 (delta) and Job 2's human lines are prompt text only; only Job 0, Job 3's
  gate and Job 4's drift check have code behind them.
