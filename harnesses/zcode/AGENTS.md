# Delegation and acceptance policy (ZCode)

Mirrors `dachent/model_proctor`. The proctor assigns the exam, watches the clock,
and grades it objectively — the model never marks its own work. Deterministic
evidence outranks every model, including you.

Orchestrator: DeepSeek V4 Flash 0731. Workers: `escalate-glm` (GLM-5.2),
`escalate-k3` (Kimi K3). Control plane: `python C:/Dev/bin/zproctor.py`.

## Most of this is enforced, not requested

Two PreToolUse hooks make these refusals real, in any permission mode including
yolo:

- **Dispatch gate** (`Agent` tool) — you cannot delegate until a lane is
  selected, cannot delegate at all on lane `self`, cannot use a worker other
  than the lane's, cannot escalate a tier without recorded stagnation, and
  cannot exceed the dispatch budget.
- **Evidence guard** (`Write|Edit|Bash|…`) — you cannot write into the
  acceptance-evidence store.

`zproctor` refuses in code too: acceptance on a failed verify, on a stale
receipt, on a tampered journal, on an out-of-scope change, and on a changed
verification surface. Do not plan around these; plan with them.

## Flow

```bash
python C:/Dev/bin/zproctor.py lane --task t1 --workspace . --bounded --known-location --objective-acceptance
```

`lane` first, always — it is what authorizes dispatch. Pass the task's
**observable features**, not a guess at difficulty:

| Features | Lane | Meaning |
|---|---|---|
| `--bounded --known-location --objective-acceptance` | `self` | you do it; no dispatch authorized |
| `--marathon`, or not `--bounded` | `k3` | `escalate-k3` |
| anything else | `glm` | `escalate-glm` |

Then `init`, declaring what may change and what must not:

```bash
python C:/Dev/bin/zproctor.py init --task t1 --workspace . --scope "src/*" --verifier python -m pytest -q
```

`init` seals the verifier surface — everything named in the verifier argv, plus
every `conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, `pyproject.toml`,
`*.pth`, `sitecustomize.py` present — copies them outside the tree and hashes
them. A sealed file that changes is **restored and flagged** at verify time. One
that *appears* after init refuses verification outright.

Then `verify`, then `accept`. Read `next` from the verify output; it is computed,
not guessed:

- `same_worker_repair` — localized defect, same worker, targeted fix.
- `lateral_switch` — three identical normalized fingerprints. Only now will the
  gate authorize the next tier.
- `accept` — passing. Run `accept`; it re-checks the tree.

Never report a task complete on a worker's self-report. Re-verify.

## Entry gate

Trivial work — known location, one edit-test cycle — you do yourself. A dispatch
pays a full context rebuild with no cache hit. Lane `self` exists for this and
the gate will refuse to let you delegate it.

## Evidence packet on a switch

When the gate authorizes a switch, hand over: objective, acceptance criteria,
scope paths, current diff, verbatim verifier output, the fingerprint, and the
switch reason. Never forward the failed worker's rationale — it propagates the
wrong frame. Its fingerprint and output are facts; its explanation is not.

Provider or tool failure (timeout, transport, unsupported parameter) is not a
capability problem. Retry or switch transport, not to a bigger model.

## Workers never own acceptance

- A worker may not create, edit, approve or expand the acceptance standard for
  its own task. The seal enforces this; scope enforces the rest.
- Never let a worker both invent a proof and certify it passed.
- No LLM is assigned to watch another LLM. There is no reviewer subagent by
  design — a model reviewer has an unmeasured error rate, and cascades beat
  baseline only when verifier error ≤ 0.1 (RouterBench, arXiv:2403.12031). The
  verifier is a command.

## Session discipline

One task per dispatch, closed at acceptance. Externalize verified state to files
as you go; do not accumulate completed task detail in your own context. On the
source project the leader session was 96% of spend ($196 of $203) against $7.23
for every worker combined. Your own context is the dominant cost — delegate to
protect it, not to buy quality. Measured on this workstation, DeepSeek V4 Flash
and GLM-5.2 both scored 30/30 hidden on evals v3, at $0.012 vs $0.097 per pass.

To resume a dead trajectory, restart from `zproctor status` plus the evidence
packet, not from conversation replay.

## Honest limits

Not replicated from model_proctor: worker dispatch through the control plane
itself (ZCode owns subagent lifecycle), per-dispatch isolated homes, and cost
metering. For unattended production dispatch that needs those, use the real
runner at `C:/Tools/model-proctor/runner.py`.

`escalate-k3`'s marathon lane is a vendor-prior hypothesis, not a measured
result — no marathon-shaped case exists in the v2/v3 corpora. Say so when you
use it.
