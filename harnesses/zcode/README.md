# ZCode harness

model_proctor for [ZCode](https://zcode.z.ai), Z.ai's agentic development environment.

## Why this harness is shaped differently

Kimi's `runner/` **owns** dispatch — it spawns workers through `delegate/`. ZCode owns
subagent dispatch itself, so the proctor cannot own it. It **gates** it instead, with a
`PreToolUse` hook in front of ZCode's `Agent` tool.

Everything else — lane selection, fingerprints, stagnation, scope, sealing — is the
shared contract (`core/decisions.py`, `policy/HARNESS_CONTRACT.md`), not a second
opinion.

## Layout

```
zproctor.py              control plane: lane, init, verify, accept, record, status, events
hooks/zproctor_gate.mjs  PreToolUse on `Agent` - dispatch authorization
hooks/zproctor_guard.mjs PreToolUse on Write|Edit|Bash - evidence boundary
roster.example.json      lane -> agent NAME (copy to roster.json; that is gitignored)
zproctor_pricing.json    Fireworks Standard serverless tier, for `record`
```

The shims are **policy-free by construction**. Ladder, dispatch budget, abort cap and
escalation threshold all arrive in `lane.json`, written by `zproctor.py` from the shared
core. `tests/test_zproctor.py::test_shim_holds_no_policy` fails if a constant leaks back
in — which is how the control plane and its own gate drifted into a deadlock once.

## Flow

```bash
python zproctor.py lane   --task t1 --workspace . --bounded --known-location --objective-acceptance
python zproctor.py init   --task t1 --workspace . --scope "src/**" --verifier python -m pytest -q
python zproctor.py verify --task t1 --workspace .
python zproctor.py accept --task t1 --workspace .
python zproctor.py record --task t1 --workspace .
```

`lane` runs first and is what authorizes dispatch at all. The lane is frozen for the
life of the task, and one live task per workspace: a second task id would otherwise buy
a fresh lane with friendlier features. Both were observed being exploited by a real
orchestrator.

ZCode has no cheap worker, so its roster binds `cheap -> self`: bounded, known-location,
objective-acceptance work is done by the orchestrator and **no dispatch is authorized**.

## Installing the hooks

`~/.zcode/cli/config.json` — the config the desktop app's spawned agent reads
(`nativeConfigDir: ".zcode/cli"`):

```json
"hooks": { "enabled": true, "timeoutMs": 5000, "events": { "PreToolUse": [
  { "matcher": "Write|Edit|Bash|MultiEdit|NotebookEdit",
    "hooks": [{ "type": "process", "command": "<node>",
                "args": ["<harness>/hooks/zproctor_guard.mjs"], "timeoutMs": 5000 }] },
  { "matcher": "Agent",
    "hooks": [{ "type": "process", "command": "<node>",
                "args": ["<harness>/hooks/zproctor_gate.mjs"], "timeoutMs": 5000 }] }
]}}
```

Notes paid for in debugging:

- **Node, not Python.** ZCode spawns the hook per tool call. Measured: Python 4.7–6.4s
  cold / 0.67–1.16s warm, Node 0.25–0.38s. A Python hook also blocked indefinitely when
  stdin stayed open.
- **`type: "process"` with an absolute interpreter and an `args` array.** Bare `python`
  is not resolvable from a GUI-spawned child on Windows, and there is no `py.exe`.
- **An invalid `model` key voids the entire config file**, silently disabling every
  hook — including other plugins' — with no visible symptom.

## Fail direction

The shims fail **open** on the fast path: a guard that cannot answer allows the tool
call and records `GATE_FAILED_OPEN`. `accept` then refuses if any occurred between init
and verify. Acceptance is the only irreversible act; tool calls are reversible and the
tree-bound receipt catches any change.

## Build-identity lock

The block contract (`hookSpecificOutput`, exit code 2) is reverse-engineered from
`resources/glm/zcode.cjs`. That reading was wrong once and cost a working guard, and
ZCode auto-updates. Record the bundle sha256; on mismatch a parity report is
`UNVERIFIED`, never PASS — an offline suite stays green while the gate is dead.
`tests/test_wire_shape.py` is the guard against that class of drift.

## Tests

```bash
python -m unittest discover -s harnesses/zcode/tests -v
```

Node-dependent cases skip when node is absent.
