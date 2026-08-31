# Harness contract v1

What every model_proctor harness must decide the same way, and what each harness is
free to decide for itself.

`contract: 1`. The reference implementation is `core/decisions.py`. A harness declares
the contract version it implements in its `roster.json`.

## Why a contract rather than shared dispatch code

Harnesses differ in **architecture**, not merely in style:

| | Kimi Code CLI | ZCode |
|---|---|---|
| Dispatch | `runner/` **owns** it, spawns workers via `delegate/` | the harness owns it; the proctor **gates** it in front of the `Agent` tool |
| Policy delivery | `SKILL.md` | `AGENTS.md` |
| Evidence boundary | state outside the workspace | that, plus a `PreToolUse` hook that holds in any permission mode |

Dispatch code therefore cannot be shared. The decisions behind dispatch are pure
functions over data, and those are shared, once, in `core/decisions.py`.

## Shared — every harness uses `core/decisions.py`

### Lane table

Roles, not model names. Evaluated **in this order**; the ordering is load-bearing.

```
1. open_ended OR marathon                              -> marathon
2. multi_module OR unfamiliar_repo                      -> substantial
3. bounded AND known_location AND objective_acceptance  -> cheap
4. otherwise                                            -> substantial
```

The marathon guard runs first. A task that is both bounded and marathon-shaped goes to
the marathon lane — a rewrite that reorders these inverts in the dangerous direction,
handing open-ended work to the cheapest tier or to no worker at all.

Features are **observable**, never a judgement of difficulty: assessing "how hard is
this" before solving it is the call a fast router is worst at.

`core/tests/test_kimi_parity.py` enumerates the full truth table — all 2^7 vectors —
against both `core.lane_for` and `runner.lane_for`. Sampling is how two implementations
of one rule stay green while disagreeing on the inputs nobody wrote a case for.

### Failure fingerprints

Normalized identity of a failure *class*, so "the same failure three times" is decidable
without a model's opinion. Addresses, timings, absolute paths, line numbers and bare
numbers are normalized out; the last 8000 bytes are hashed.

### Stagnation and budgets

| | |
|---|---|
| `STAGNATION_THRESHOLD` | 3 identical fingerprints before a lateral switch is legal |
| `DEFAULT_MAX_DISPATCHES` | 3 |
| `DEFAULT_MAX_STAGNANT` | 6 identical fingerprints before terminal abort |

`next_action(rc, fingerprints, max_stagnant)` returns
`accept | abort | lateral_switch | same_worker_repair`. **A passing verify clears the
run.** That is stated here because it did not hold once: the control plane kept counting
across a green verify while its own gate cleared, so the plane ordered an escalation the
gate refused. Deadlock, with a 51-test suite green — no case had a pass-then-fail
journal.

### Scope

`in_scope` / `scope_violations`. `**` spans separators, `*` does not, `?` is one
non-separator character. A bare directory name covers everything beneath it — but only
when the pattern contains no glob characters, or that fallback silently defeats
single-level `src/*`.

### Verification surface

`conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, `pyproject.toml`,
`sitecustomize.py`, `usercustomize.py`, `*.pth`. If one of these **appears or changes**
after init, the verifier is no longer the exam that was agreed to, and verification is
refused rather than run against a different one.

## Bound per harness — the roster

`roster.json` maps contract roles to concrete worker **names**, plus the ordered ladder:

```json
{ "contract": 1,
  "ladder": ["cheap", "substantial", "marathon"],
  "lanes": { "cheap": "self", "substantial": "escalate-glm", "marathon": "escalate-k3" } }
```

Rules:

- **Names only, never model ids.** Model and argv binding stays in the machine-local,
  ACL-hardened harness config. `delegate/agents.json` is gitignored and hardened
  deliberately (#63); putting model ids in a tracked file in a public repo would
  reverse that and create a third source of truth.
- `cheap -> "self"` means the harness has no cheap worker: the orchestrator does the
  task and **no dispatch is authorized**. `self` is a binding, not a lane.
- The ladder defines the only legal lateral switch: one step, in order.
- `harnesses/*/roster.json` is gitignored; `roster.example.json` is tracked.

A harness with a different model mix — Codex — is a different `roster.json` and zero
contract change.

## Fail direction

Declared per gate, because the two current harnesses are **opposite** and would
otherwise both be "conformant" while disagreeing on whether a gate is a gate.

| harness | fast path | acceptance |
|---|---|---|
| Kimi | fail **closed** (`raise SystemExit`) | closed |
| ZCode | fail **open** (deadline, then allow) | **closed** — see below |

ZCode's fail-open is bounded, not unconditional: a guard that cannot answer allows the
tool call and records `GATE_FAILED_OPEN`, and `accept` refuses if any occurred between
init and verify. The only irreversible act is acceptance; tool calls are reversible and
the tree-bound receipt catches any change. So the irreversible boundary is guarded hard
and the reversible path stays fast, rather than paying a stall on every tool call and
letting one guard crash brick a session.

## Verifier hygiene

A verifier must run with a cleared bytecode cache (`-B` or
`PYTHONDONTWRITEBYTECODE=1`). Two source variants of identical byte length written in
the same second let CPython reuse a stale `.pyc`, and a **failing** verify then reported
`next: accept`. Observed while writing these tests.

## Build-identity lock — SPECIFIED, NOT YET IMPLEMENTED

Where a harness's enforcement path is reverse-engineered from a vendor bundle — ZCode's
`hookSpecificOutput` contract is read out of `resources/glm/zcode.cjs`, and that reading
was wrong once already, costing a working guard — the harness records the bundle's
sha256. On mismatch, parity reports `UNVERIFIED`, never PASS: an offline suite stays
green while the gate is dead.

**Status: specification only.** No harness records a bundle hash today and no
`selftest` exists. It is written here so the requirement is not lost, and labelled
rather than implied — a contract clause with no consumer is the failure mode #36
(`tamper_detected` detected-never-consumed) and #58 (a report-only field that "reads
as protection and provides none") are about. `harnesses/zcode/tests/test_wire_shape.py`
covers the adjacent risk — that the shape we emit is one ZCode accepts — but cannot
detect that the vendor bundle changed underneath it. Tracked in #69.

## What a new harness must supply

1. `roster.example.json` declaring `contract`, `ladder`, `lanes`.
2. A control plane that calls `core/decisions.py` for every rule above.
3. Any native shim it needs, containing **no policy** — every constant arrives from the
   control plane. `harnesses/zcode/tests/test_zproctor.py::test_shim_holds_no_policy`
   asserts this for ZCode; a new harness should assert the same.
4. A wire-shape test against its harness's real hook/tool contract, with negative cases.
5. A row in `core/tests/test_kimi_parity.py`-style parity coverage if the harness has
   its own lane implementation rather than calling the core directly.

## Rejected alternatives

Recorded per AGENTS.md: design decisions that survive debate are written down with the
alternatives and the reason, so a later reader does not re-argue a settled question or
re-adopt a design that was tried and failed.

### Conformance corpus with per-harness adapters

JSON cases (`input -> expected decision`) plus a thin adapter per harness, run as a
cross-product. **This was the original design and it was built up to the point of
failing.** Rejected because:

- ZCode's enforced rules live in JavaScript. A Python adapter answering the budget and
  stagnation cases would be a **third** implementation of those rules, inside the very
  mechanism whose purpose is to prove there is one.
- A `case in -> decision out` shape cannot represent side effects, sequences, filesystem
  semantics, wire shape, or fail direction — so two harnesses could be 100% conformant
  while disagreeing on whether a gate is a gate.
- The corpus was justified as a way to *discover* divergence. Discovery was never the
  bottleneck: reading both implementations found roughly eleven divergences in under an
  hour, and one drift had already occurred *inside a single harness* while a 51-test
  suite stayed green.

Replaced by: one decision core, policy-free native shims, an exhaustively enumerated
lane truth table, and a wire-shape test.

### A shared library that every harness imports

Rejected in the first draft on the grounds that "a shared Python module buys nothing if
a future harness's adapter is not Python". That reasoning was **wrong**, and the
correction is the current design: `zproctor_gate.mjs` is a Node shim that reads
`lane.json` and `events.jsonl` written by Python. A shared core does not need to be
*importable* — it needs to be **runnable or persistable**. Hermes and Codex can consume
`core/decisions.py` the same way regardless of their implementation language.

### No parity mechanism — let harnesses diverge deliberately

Rejected. Parity is the research claim this repo exists to support; without a consumer
it is an assertion. This repo's own bug record is precisely about mechanisms with no
consumer — #36 (`tamper_detected` detected-but-never-consumed) and #58 (a report-only
field that "reads as protection and provides none"). GitHub issues are not a consumer.

### Separate repositories per harness

Rejected. It removes the drift problem by removing the ability to observe it, and the
shared decisions — lane table, fingerprints, stagnation thresholds — would be copied
into each repo with no mechanism holding them equal.

### `self` as a shared lane

The first draft made `self` a fourth lane in the contract. Rejected: it is what a
harness binds `cheap` to when it has no cheap worker, i.e. a **roster binding**. As a
lane it silently changed Kimi's semantics and inverted in the dangerous direction — a
task declared `{bounded, known_location, objective_acceptance, marathon}` would have
gone from Kimi's `k3` to `self`, meaning **no dispatch at all for a marathon task**. It
also erased Kimi's cheap worker tier, which exists on measured evidence (#23).

Keeping `self` in the roster is what lets contract v1 land with **zero changes to
`runner.py`**.

### Model ids in the roster

The first draft put `"custom:fireworks/glm-5p2"` in a tracked `roster.json`. Rejected:
`.gitignore` untracks `delegate/agents.json` and `install.py` ACL-hardens it on purpose
(#63), so a tracked file in a public repo reverses that decision and creates a third
source of truth alongside `DEFAULT_AGENT_MAP` and `agents.json`. Rosters carry **names**;
model and argv binding stays machine-local.

### Pure fail-closed for ZCode

Rejected. It would cost the internal deadline on every `Write`/`Edit`/`Bash` and let a
single guard crash brick a session, while buying nothing the acceptance gate does not
already provide: acceptance is the only irreversible act, and tool calls are reversible
because the tree-bound receipt catches any change. Hence bounded fail-open — see
**Fail direction**.

### Renaming Kimi's internal lane ids in the harness work

Rejected as a side effect. The literal string `flash` is keyed on by
`flash_lane_forbidden_production_runner` (written from a recorded incident), the README
refusal table, TOOL-019/#39, and `SKILL.md`'s trigger text; `LANES` carries a freeze
comment gated on #26. Tracked separately in #71, and
`test_lane_ids_are_not_silently_renamed` pins the current ids so it cannot happen by
accident.
