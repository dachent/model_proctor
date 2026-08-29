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

## What a new harness must supply

1. `roster.example.json` declaring `contract`, `ladder`, `lanes`.
2. A control plane that calls `core/decisions.py` for every rule above.
3. Any native shim it needs, containing **no policy** — every constant arrives from the
   control plane. `harnesses/zcode/tests/test_zproctor.py::test_shim_holds_no_policy`
   asserts this for ZCode; a new harness should assert the same.
4. A wire-shape test against its harness's real hook/tool contract, with negative cases.
5. A row in `core/tests/test_kimi_parity.py`-style parity coverage if the harness has
   its own lane implementation rather than calling the core directly.
