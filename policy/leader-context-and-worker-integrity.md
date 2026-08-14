# Leader-context and worker-integrity fixes plan (2026-08-13, v2 — post-adversarial-review)

**Problem.** Today's metered data (snapshot ~20:15 local): the K3 leader session cost ~$194
(~96% of spend — restated from "95%" per evidence review), dominated by 506M cached-read
tokens — context compounding over an all-day session. The fix direction is smaller leader
context. The risk introduced: information crossing the worker→leader boundary as summaries
can miss, lose, or misrepresent content. Guards must be architectural (non-LLM), not more
review layers. (Meter snapshot is a live, moving figure; see `.orchestrator/tmp/meter_today.py`
output — cost figures here are as of that timestamp.)

**Adversarial review (5 GLM reviewers, 2026-08-13) found the v1 plan overstated what exists:**
the envelope has no files-changed/commands-run fields, "full logs" cap silently at 64 MiB
(below the 86MB incident's size), the controller can't touch the tracker/git surface where
both incidents happened, and self-set review standards are a governance hole. v2 below is
the adjudicated version: every fix is marked [CODE] (built now), [POLICY] (skill text),
or [RESIDUAL] (honestly out of scope for automation).

## The fixes

### F1. Deterministic extraction for fact classes [CODE + POLICY]
Facts that exist as structured records (session logs, CI logs, audit trails) are extracted
mechanically FIRST; LLMs interpret extractions, never scan raw volume.
- [CODE] New `scripts/extract_log.py`: parses the kimi session-log event format into a
  mutations/writes/commands listing AND emits a **coverage manifest** (bytes in, records
  parsed, records unrecognized/dropped, truncation flags). Leaders reject extractions whose
  coverage shows nonzero unrecognized records above a small threshold. Extractor code is
  verifier-class: hash-frozen per goal; changes force re-review.
- [POLICY] The digest rule stays in the skill; it now names the extractor as the mechanism.

### F2. Evidence-bearing worker results [CODE — the fields did NOT exist]
- [CODE] cascade.py computes `files_changed` itself after each dispatch (git status vs the
  checkpoint, INCLUDING an untracked-file inventory — the stash ref alone is blind to new
  files), records `child_exit_code` in the evidence entry, and stores it in the task's
  evidence list.
- [CODE] delegate envelope gains `stdout_log_truncated` / `stderr_log_truncated` (the
  booleans exist internally; this is plumbing). [POLICY] any extraction/verdict drawn from
  a truncated log is void until re-acquired. Raise `max_log_bytes` for log-heavy agents.

### F3. Verdicts with pointers; leader reads raw by exception [CODE + POLICY]
- [CODE] cascade.py archives the run_dir logs into `<workspace>/.orchestrator/evidence/`
  at dispatch completion and records `stdout_sha256`/`stderr_sha256` in state — pointers
  stop being silently stale (temp cleanup, pruning). delegate README's delete-after-inspect
  advice is amended to "after the caller confirms evidence has been archived."
- [POLICY] leader reads raw evidence only at decision points or spot checks.

### F4. Controller-owned state and dispatch [CODE — honestly rescoped]
The controller enforces dispatch, caps, verification, and (new) commit-on-green for worker
output. It does NOT and will not perform tracker writes, PR management, or leader git
operations — those remain leader actions governed by F5-F7 policy. The claim is narrowed
accordingly. (Had today's manual work gone through the controller, the code-enforced QC cap
would have stopped the over-depth loop — that is the demonstrated value.)

### F5. Spot-verification duty, made non-discretionary [POLICY]
Selection is not the leader's choice: verify (a) the claim the acceptance decision depends
on (identified mechanically — verifier target paths, largest diff hunk) plus (b) one
uniform-random claim. One failed spot check → full verification of that task's claims, not
a re-sample. Verdicts logged in cascade-state.json so "0 failures" is auditable.
[RESIDUAL] the spot check is still a leader reading; entailment is not automatable here.

### F6. Durability [CODE — corrected per review]
v1's "commit at green" collides with the repo canon and writes unverified states into
history. Corrected: (a) [CODE] the controller's dangling `git stash create` checkpoint (which
SURVIVES a checkout) becomes the per-dispatch durability mechanism, extended with the
untracked-file inventory; (b) [CODE] `cascade.py commit-green` (opt-in, after controller-run
verify passes — never worker-reported green) commits scope paths to the working branch, used
only with the standing user confirmation for that goal. v1's incident (uncommitted composite
wiped by a leader-side checkout) is prevented by (a) alone.

### F7. Shared-tree git safety [POLICY — rewritten as protocol, not ban]
Full-tree branch-switching checkout/restore/clean in a shared tree while a worker is active:
banned. Scoped rollback via `git checkout <checkpoint_ref> -- <scope paths>` is ALLOWED when
all workers are in terminal envelope states. Historical reads use `git show <ref>:<path>`.
[CODE] `cascade.py rollback --task T` performs the scoped rollback so code enforces the
protocol's legal path.

### F8. Review depth proportional to threat model — with the governance hole closed [CODE + POLICY]
v1 let the leader self-set the standard and self-dismiss findings. v2:
- [CODE] `threat_model` is an owner-set field at `cascade.py init` (controller-validated
  enum: `single-operator | adversarial-local | hostile-input`), immutable for the goal.
- [CODE] any dismissal of a blocker/major QC finding under the depth rule must be recorded
  via record-qc with the finding verbatim; the final report to the user must include
  dismissed findings verbatim (controller requires them in the goal's evidence record).
- [POLICY] for financial controls, local-tamper findings default to major and are NOT
  stop-signal eligible. The single-operator default defends wrong-data/process failures.

### F9. Leader-session cost discipline [POLICY + CODE-lite — corrected per review]
v1 oversold: the config flip to GLM-primary (already done) captures ~53% of the cached-read
pool by repricing; the residual is turn-count × context-discipline, owned by F1/F3, not
session lifetime. Task-scoped sessions win only when steady context C > ~3-5× the canon
rebuild S (break-even: C/S > (10 + k − 1)/k at K3 rates) — so:
- [POLICY] default is compaction-aware long sessions (one per program); start a new session
  only when the current session's context has grown past ~3-5× the resume canon, or at
  natural task boundaries.
- [CODE] `cascade.py handoff` emits a bounded bootstrap packet (hard byte cap,
  deterministically generated from state + the decisions log) for the next session.
- [CODE] cascade-state.json gains an append-only `decisions` log (decision, rationale,
  rejected alternatives, source=user|leader) so session boundaries stop losing rationale.
- [CODE] meter.py gains a `session_rebuild` token class so F9's claimed savings get MEASURED
  (first-N-turns cacheCreate/cacheRead per new session) before adoption claims are made.

### Residuals (honest, not solved)
[RESIDUAL] Entailment and completeness are not boundary-checkable: omission of real events,
true-but-irrelevant evidence, and aggregate miscomposition (the digest incident's actual
shape) survive every guard. These remain a leader/human duty — the plan claims to reduce
their frequency, not eliminate them.
