# Model rotation runbook

How to swap the model behind a worker seat without silently breaking dispatch, metering,
or the record of what was measured. A *rotation* changes three things and nothing else: the
`[models.*]` block in kimi's config, the pricing row, and the `-m` value inside one seat.

Everything else is frozen:

| Frozen | Where | Why |
|---|---|---|
| Lane ids `flash` / `glm` / `k3` | `core/decisions.py`, `runner.LANES` | The lane table and every harness key on them |
| Seat ids `marathon-worker` / `substantial-worker` / `expert-worker` | `runner.DEFAULT_AGENT_MAP`, installed `agents.json` | They are the dispatch interface — role names since the 2026-10-26 rename (owner-directed). See the comment above `DEFAULT_AGENT_MAP` and [Seat naming](#seat-naming--decided-2026-10-26) |
| CLI surface of `runner.py` / `delegate.py` / `catalog.py` | those files | Briefings and the installed SKILL.md quote it |

Paths used below:

- Repo (`dachent/model_proctor`): `C:/Dev/bootstrap-state/model-proctor/repo-1017/model_proctor`
- Installed plane: `C:\Tools\model-proctor\`
- Live harness config (G5-protected): `~/.kimi-code/config.toml`

Two owners only. **Agent** — reversible, local, no elevation. **Decision seat** — the owner
acting with G5 and filesystem-elevation authority. A step that needs the decision seat cannot
be delegated down; a step marked agent should not be escalated up.

| Phase | Owner | Irreversible? |
|---|---|---|
| 0 Pre-flight | agent, decision seat authorizes | no |
| 1 Catalog | decision seat (G5) | reversible, but elevated |
| 2 Price | agent | no (append-only) |
| 3 Probe | agent | no (spends tokens) |
| 4 Bind | decision seat (elevated) | yes — overwrites the live roster |
| 5 Deploy | agent | yes — overwrites the installed plane |
| 6 Verify | agent | no |
| 7 Measure | agent, doctrine-bound | no |
| 8 Record | agent | no |
| Rollback | decision seat (elevated) | restores from the Phase 4 backup |

---

## Phase 0 — Pre-flight

**0.1 File the rotation issue** — decision seat decides, agent files.

Per the backlog convention: `[AREA-NNN]` key, `priority:*` label, `## Blocked by`, and a
`## Final evidence and handoff` before closing. Name the lane, the retiring id, the incoming
id, the date, and the sentence *the incoming arm is UNMEASURED*.

- Gate: the issue exists and is linked from the Phase 8 commit. Nothing below runs without
  it — under the durability doctrine (owner directive 2026-08-25) the issue is the only
  durable record of why the roster looks the way it does.

**0.2 Pin the exact id string** — agent.

```bash
firectl serverless list        # C:\Users\<you>\bin\firectl
```

Copy the standard-tier `input` / `cached_input` / `output` for that exact id.

- Fireworks ships **dated snapshots**, and the undated alias is a different deployment at a
  different price. `deepseek-v4p1-flash` (undated, 2026-09-10) and
  `deepseek-v4p1-flash-1017` (2026-10-17) are not interchangeable. Picking the wrong string
  is a silent metering error, not a crash.
- The price history in `evals/pricing.yaml:26-39` is the cautionary record: `glm-5p3-flash`
  was entered at the GLM 5.2 Fast row and flash-lane metering ran ~14x high from
  2026-08-28 to 2026-09-09 (#83 M3). Manual transcription fails; copy the numbers into the
  issue in the same edit that writes the YAML.
- Gate: exact id string, its three prices, and the snapshot date recorded in the issue.

**0.3 Protect local patches before any installer run** — agent.

```bash
cd C:/Dev/bootstrap-state/model-proctor/repo-1017/model_proctor
git diff --no-index harnesses/kimi-code/runner/runner.py C:/Tools/model-proctor/runner.py
# repeat for delegate.py, pilot.py, pricing.yaml
```

This is not ceremony. A real 287-line `cmd_attest` / `cmd_tree_sig` patch was applied to the
installed `runner.py` and is archived at
`C:/Dev/bootstrap-state/model-proctor/local-attest/` (`attest-delta.patch`, 287 lines, with
`runner.py.installed-20261026` and `delegate.py.installed-20261026`). It is **not** at `HEAD`
and **not** in the current install, so that archive is its only copy — and it lives on
`C:\`, which is ephemeral by doctrine. A rotation is precisely the moment it is most likely
to be overwritten and lost.

Mechanical backstop: `scripts/install.py` refuses to overwrite any target that differs from
both the repo source and `C:\Tools\model-proctor\install-manifest.json`, printing a bounded
diff and exiting `3` (`REFUSED_LOCAL_PATCH`). It deliberately excludes `agents.json` from
that guard (a differing roster is its normal state) — which is why Phase 5 has its own check.

- Gate: the diff is empty, **or** the differing content is committed or filed as an issue and
  the issue says which. If `install.py` later refuses, the answer is commit-or-file, never
  `--force`.

---

## Phase 1 — Catalog

**1.1 Add the `[models."fireworks/<new-id>"]` block** — decision seat.

`~/.kimi-code/config.toml` is G5-protected: either the owner does this, or it happens under a
documented bypass with non-Kimi review. **Hand-build the block** by copying an existing
`[models."fireworks/..."]` entry and changing only the header and `model`.

`kimi provider catalog add fireworks` does not work — it fails with:

```
Provider fireworks not found in catalog at https://models.dev/api.json
```

**Never delete the outgoing block at rotation time.** It is the rollback path and the only
way to re-bind an old seat without re-deriving its config.

- Gate: `python C:/Tools/model-proctor/catalog.py --json` lists the id. It should report
  `meters_as: "unknown"` at this point — the price row is Phase 2's job, and the catalog is
  supposed to show you the gap. `catalog.py` reads `config.toml` live at invocation
  (`delegate.live_harness_models()`, `delegate.py:379-398`), so there is no copy step and no
  second catalog to desync (#23).

---

## Phase 2 — Price

**2.1 Append the pricing row** — agent.

In `evals/pricing.yaml`, append `fireworks/<new-id>: { input: X, cached_input: Y, output: Z }`
with a dated comment naming the rotation, the source (`firectl serverless list`, dated), and
the snapshot date. Match the existing voice — the file's comments are the audit trail.

Retired rows stay forever. Historical metering depends on them: repricing a retired id
rewrites the cost of runs that already happened, which is the one thing this table must never
do (#83 M3 republishes corrected estimates separately instead of editing history).

- Gate: `python -m unittest discover -s evals/tests -v` green.
- Checkpoint: `catalog.py` now reports `meters_as: "priced"` for the id.

---

## Phase 3 — Probe before bind (spends real tokens)

**3.1 Dispatch the id directly, once** — agent.

```bash
python C:/Tools/model-proctor/delegate.py --model fireworks/<new-id> \
  --workspace C:/Dev/workspaces/rotation-probe \
  --task "Reply with the single word: ok"
```

A catalog-listed id can still 404 at dispatch. On 2026-08-28 `glm-5p3` was listed and
returned `404 Model not found, inaccessible, and/or not deployed` in the morning, going live
later the same day.

This step runs **last among the unbound steps, immediately before bind**, so the window
between "proven dispatchable" and "bound into a seat" is minutes rather than hours. A probe
run right after Phase 1 proves yesterday's catalog, not today's deployment.

- Gate: the envelope shows a real completion carrying the model's answer — not `404`, not
  `invalid`, not an auth error.
- On 404: stop. There is nothing to roll back yet, and no price row lies to you about a dead
  id. Do not "just try the undated alias" — go back to 0.2 and pin it properly.
- The probe is un-metered (no `runner.py record`), which is exactly why it must be trivial.

---

## Phase 4 — Bind (elevated; overwrites the live roster)

`C:\Tools\model-proctor\agents.json` ships `BUILTIN\Administrators:(F)`,
`NT AUTHORITY\SYSTEM:(F)`, `<DOMAIN>\<user>:(R)`. Editing it as-is fails with Access Denied.

**4.1 Grant yourself write** — decision seat.

```bash
icacls "C:\Tools\model-proctor\agents.json" /grant "%USERDOMAIN%\%USERNAME%:(M)"
```

This is the same dance `install.py` performs internally when it needs to overwrite a changed
roster — its `_icacls(str(agents_dst), "/grant", f"{principal}:(M)")` call is the reference
implementation.

- Gate: `icacls` reports `Successfully processed 1 files`.

**4.2 Back up the roster** — decision seat.

```bash
cp /c/Tools/model-proctor/agents.json \
   "/c/Tools/model-proctor/agents.json.bak-$(date +%Y%m%d)-pre-<new-id>"
```

Use `cp`. `copy` is a cmd.exe builtin and does not exist in Git Bash — under the Bash tool it
fails, and a failed backup that nobody re-checks is worse than no backup. (PowerShell:
`Copy-Item`.)

- Gate: the backup exists and is non-empty.

**4.3 Edit one string** — decision seat.

Change only the rotated seat's `-m` value. Seat ids never change inside a rotation.

- Gate: `python -m json.tool C:/Tools/model-proctor/agents.json > /dev/null` parses; the seat
  id still resolves to the new id; a diff against the 4.2 backup shows exactly one changed
  line.

**4.4 Record the temporary exposure.**

Between 4.1 and Phase 5 the roster is writable by the installing user. Phase 5's install
re-hardens it and prints the confirmation. If the rotation pauses here, re-harden by hand:

```bash
icacls "C:\Tools\model-proctor\agents.json" /inheritance:r \
  /grant:r "BUILTIN\Administrators:(F)" /grant:r "%USERDOMAIN%\%USERNAME%:(R)"
```

- Gate: never leave a rotation half-done across a session boundary with the roster writable.

---

## Phase 5 — Deploy

**5.0 Stop: the repo-side roster hazard** — agent.

`scripts/install.py` copies `harnesses/kimi-code/delegate/agents.json` over
`C:\Tools\model-proctor\agents.json` whenever the two differ. In this working tree that file
is a byte-identical copy of `agents.example.json` — the `example-coder` placeholder roster.
Deploying through it replaces the live three-seat roster, and every lane dispatch then
refuses with an unknown-agent error. The installer's patch guard excludes `agents.json` on
purpose, so nothing mechanical catches this.

```bash
diff -q harnesses/kimi-code/delegate/agents.json \
        /c/Tools/model-proctor/agents.json \
  && echo "same — safe to deploy" \
  || echo "DIFFERS — move it aside before deploying"
```

- Gate: identical, absent, or moved aside. This check runs before **every** install, not only
  during a rotation — it is the same failure in every context.

**5.1 Install** — agent.

```bash
python scripts/install.py
```

- Gate: the exit code **is** the gate.
  - `0` — every `REQUIRED_AFTER_INSTALL` path reported present.
  - `1` — incomplete install. Do not proceed.
  - `3` — `REFUSED_LOCAL_PATCH`: an installed file holds content that exists in no commit.
    Commit or file it, then re-run. `--force` deletes it; that is the only thing it is for.
- Also check stdout for `agents.json ACL hardened (Admins:F, <principal>:R)` with no ACL-leak
  warning, and that `install-manifest.json` was rewritten.

---

## Phase 6 — Verify

**6.1 The seat's model is priced** — agent.

```bash
python C:/Tools/model-proctor/catalog.py
```

- Gate: the new id shows a real per-1M price and `meters_as: "priced"`, not `"unknown"`.

**6.2 One cheap live dispatch through the runner** — agent.

```bash
python harnesses/kimi-code/runner/pilot.py --cases <smoke-case-id> --lane <lane> --max-dispatches 1
```

- Gate: the dispatch completes and the run directory contains a `wire.jsonl`.

**6.3 Meter it, on the record row** — agent.

```bash
python C:/Tools/model-proctor/runner.py record --task <task.json> \
  --workspace <ws> --wire <run>/wire.jsonl \
  --pricing C:/Tools/model-proctor/pricing.yaml
```

- Gate: inspect the emitted row (printed, and appended to `<state>/tasks.jsonl`).
  - `"usage_unknown": false`
  - `"api_cost_usd"` is a number, not `null`
  - `"tokens_by_model"` names the new id

`runner.py status` is **not** a substitute. `cmd_status` emits neither `usage_unknown` nor
`api_cost_usd`; both keys are written by `cmd_record` (`runner.py:2053-2126`), and
`api_cost_usd` only when `--pricing` is passed. A gate written against `status` can never
pass, and an operator facing an unpassable gate either stalls or quietly redefines it.

Reading the failure shapes:

| Row says | Means | Fix |
|---|---|---|
| `usage_unknown: true` | the wire has no usable `usage.record` | inspect the wire; the price row is not the problem |
| `api_cost_usd: null`, `usage_unknown: false` | the id has no row in the pricing file you passed | Phase 2, or wrong `--pricing` path |
| no row at all, `wire_required_for_dispatched_task` | `record` ran without `--wire` on a task that dispatched | pass the dispatches' wire; A13 forbids the cost-less row |

---

## Phase 7 — Measure

**7.1 Pre-registered eval** — agent, doctrine-bound.

Run the rotation arms exactly as `evals/PREREG-v3-rotation.md` (issue #91) pins them: fixed
arms, `--max-dispatches 1`, lane override, wire-metered through `record --pricing` with the
corrected table, 3 reps per case, fresh fixture workspace per rep. The pre-registration is
the command authority — do not improvise flags, and do not add arms mid-run.

- Gate: results land as committed evidence rows, and the rotated arm is labelled **UNMEASURED**
  in the README until its row exists. The retired model's numbers never carry over: the flash
  lane's `$0.0096`/hidden-pass belongs to `glm-5p3-flash`, and the marathon hypothesis to
  `kimi-k3`. An unmeasured arm is not a cheaper arm; it is an unknown.

---

## Phase 8 — Record

**8.1 One commit** — agent.

- `evals/pricing.yaml` — the new row and its dated comment.
- `README.md` — roster table row, and the break-even marked for the arm as unmeasured.
- `harnesses/kimi-code/runner/runner.py` — the lane comment block above `DEFAULT_AGENT_MAP`,
  if it names models.
- `harnesses/kimi-code/roster.example.json` — only if a seat id moved. It should not have.
- The installed-skill `SKILL.md` command examples, if the rotation changed one.

- Gate: CI green on `windows-latest`. Green on `ubuntu` would lie — the control plane is
  Windows-native.

**8.2 Close the issue** — agent. `## Final evidence and handoff` must carry the probe
envelope, the `record` row, and the eval row. Anything not on GitHub is ephemeral.

---

## Rollback

Decision seat, elevated. Two cases.

**A — bound, not yet measured** (the common one)

1. `icacls "C:\Tools\model-proctor\agents.json" /grant "%USERDOMAIN%\%USERNAME%:(M)"`
2. `cp "/c/Tools/model-proctor/agents.json.bak-<date>-pre-<id>" /c/Tools/model-proctor/agents.json`
3. Re-harden the ACL — run the Phase 5 install (after the 5.0 check), or the 4.4 `icacls` line.
4. `python C:/Tools/model-proctor/catalog.py` — the old id is listed and priced.
5. Re-run 6.2 and 6.3 against the old seat.

Nothing else rolls back, because Phases 1 and 2 never delete the retired config block or the
retired price row. That is the entire reason deletion is forbidden there.

- Gate: catalog shows the old id priced, and one smoke dispatch meters with
  `usage_unknown: false`.

**B — catalog block added, nothing bound yet**

Remove the `[models."fireworks/<new-id>"]` block (decision seat, G5). No seat references it,
and the appended price row is harmless: `catalog.py` only lists ids present in live config, so
a price row for an unbound id is invisible, not misleading. Do not delete the row — the
append-only rule has no exception for mistakes, and the diff is the record.

- Gate: `catalog.py` no longer lists the id; nothing else changed.

**Rehearse it.** Run path A once against a sandbox seat before you need it. An unexercised
rollback is a plan, not a capability.

---

## What this runbook must prevent

| # | Failure | Guardrail | Phase | Type |
|---|---|---|---|---|
| 1 | Alias missing at dispatch — a catalog-listed id 404s | direct `delegate.py --model` probe immediately before bind; `delegate`'s existing live-catalog refusal stays as the backstop | 3 | manual gate + runtime refusal |
| 2 | No price row, so the seat meters as `usage_unknown` | `catalog.py` priced check, then the `record --pricing` row gate (`api_cost_usd` non-null); A13 makes an unobserved call unknown, never `$0` | 6.1, 6.3 | manual gate + runtime refusal |
| 3 | Installer destroys uncommitted local work | Phase 0.3 diff/archive gate; `install.py` exit `3` patch refusal against `install-manifest.json`; Phase 5.0 roster check (the guard's one blind spot) | 0.3, 5.0 | installer check + manual gate |
| 4 | A seat id drifts back to naming its model | seat ids are ROLE names as of the 2026-10-26 rename (`marathon-worker` / `substantial-worker` / `expert-worker`); an id naming a model is a defect, and a rename is still its own owner-signed PR | all | documented policy |
| 5 | `roster.example.json` drifts from `DEFAULT_AGENT_MAP` | `scripts/tests/test_roster_parity.py` (landed on `fix/measurement-integrity`). It caught a real case: `cheap` sat on `ds-flash-worker`, an id the 2026-10-17 rotation retired, until someone diffed the two files by hand | — | CI test |
| 6 | `SKILL.md` quotes a command that does not exist | argparse lint over quoted `runner.py` lines (to add). The known instance is fixed: `SKILL.md` now runs `status --workspace <w>` and states that `status` takes no `--task` | — | CI test, partial |
| 7 | The seat → model binding is untracked and undocumented | the rotation issue plus this runbook; the binding is machine-local by design (`agents.json` embeds paths and must not be mirrored) | 0.1, 8.2 | documented gate |

Two things deliberately **not** guardrails:

- **README↔map parity** must stay optional. The README roster lists `gpt-oss-worker` and
  `k27-scout`, seats present in neither `DEFAULT_AGENT_MAP` nor `agents.json`. A strict test
  is red on day one and would be silenced rather than fixed, which teaches people to ignore
  the suite.
- **`runner.py status`** as a metering gate. It cannot report what it does not emit.

---

## Seat naming — decided 2026-10-26

Seat ids are **role names** as of 2026-10-26 (owner-directed): lane `flash` →
`marathon-worker` (the cheap seat you can afford to run long), lane `glm` →
`substantial-worker` (the engineering middle), lane `k3` → `expert-worker` (the most
capable). This supersedes the earlier same-day comment above `DEFAULT_AGENT_MAP`, which
recorded the opposite decision ("deliberately NOT renamed"); that comment was rewritten in
the same change, so the code and this document agree.

What the rename bought, and what it did not:

- **Readability, not stability.** Do not claim otherwise. Role names drift exactly as model
  names do: the 2026-09-09 Flash/Fast price correction and the 2026-10-26 swap both moved
  which model occupies the cheap tier. When the cheap tier's occupant changes, a role id
  misleads exactly as a model id did — with identical rename blast radius. The gain is that a
  reader no longer infers a model from an id.
- **A further rename is still its own owner-signed PR.** The ids are the dispatch interface;
  they change by decision, never as a side effect of a model swap.
- **The durable fix is not the rename.** It is (i) the CI parity test that makes
  `roster.example.json` and `DEFAULT_AGENT_MAP` disagree loudly, and (ii) the installer
  manifest guard that refuses to overwrite uncommitted work. Both are small, both are already
  specified above (guardrails 3 and 5), and neither needs a migration.
- **A missed rename fails loudly** — dispatch looks the seat id up in `agents.json` and
  refuses. Loud is the right shape, but for a queued task it is still an outage: the rename
  lands in the repo first and in the machine-local `agents.json` at Phase 5 of a rotation, so
  the two must be deployed together. Do not cite #73 as rename precedent: the in-repo #73
  references (the A1/A2/A5 comments in `runner.py`, plus the C-series ones) are the
  overnight-stall / zero-dispatch incident.

**One collision the new names create**, and it is a trap for readers: the ladder ROLE
`marathon` (a `core/decisions.py` contract role) maps to the k3 lane, whose seat is
`expert-worker`, while the seat id `marathon-worker` belongs to the **flash** lane. Ladder
roles are a separate vocabulary and are due their own rename; never read a seat id as a role.
`harnesses/kimi-code/roster.example.json` carries that warning in its `_comment`.

**Parameterization** (resolving seat → model at dispatch time) is possible, and half of it
already exists: `runner.py dispatch --agent-map <json>` overrides `DEFAULT_AGENT_MAP`.
It is **not worth it for three seats** — a machine-local file that relocates three literals
adds a thing to keep in sync and does nothing a rotation doesn't already do. That is a "not
now", not an "impossible". Note also that `core/decisions.py` is not in the way: its lane
table is keyed by cheap/substantial/marathon roles and never reads seat ids, so
parameterization does not threaten `core/tests/test_kimi_parity.py`.

---

## Provenance

This document supersedes a first draft that could not be executed as written: it edited the
ACL-hardened roster without granting write first, used `copy` under Git Bash, and gated
verification on `runner.py status`, which emits neither of the fields the gate checked. The
287-line attest patch it referenced was reported missing and is in fact archived at
`C:/Dev/bootstrap-state/model-proctor/local-attest/`; Phase 0.3 exists because that archive is
the only copy.
