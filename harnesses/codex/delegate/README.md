# Codex catalog delegate

This is a single-dispatch adapter for Codex. It selects only the model or
named preset the operator explicitly requests. It does not route by lane,
task shape, cost, or any other inferred property.

## Local setup

Copy [`local-config.example.json`](local-config.example.json) to an untracked
local schema-1 configuration file outside the repository (or another location
you keep untracked). The tracked example contains the only supplied presets:

| preset | model | effort |
|---|---|---|
| `luna` | `gpt-5.6-luna` | `medium` |
| `terra` | `gpt-5.6-terra` | `high` |
| `astra` | `gpt-6-astra` | `max` |

Select either an explicit model or one preset. A preset requires `--config`.
The adapter validates the requested model and effort against live Codex
`app-server` `model/list` data before dispatch; a catalog mismatch refuses.

```powershell
python harnesses/codex/delegate/delegate.py `
  --preset terra --config C:\safe-local\codex-local-config.json `
  --transport cli --codex-executable C:\path\to\codex.exe `
  --workspace C:\work\checkout --task-file C:\safe-local\task.txt
```

The prompt must be in an external `--task-file` that resolves outside
`--workspace`; it is intentionally not accepted from the workspace or as an
inline prompt. The default sandbox is truly `read-only`. `--write` explicitly
changes it to `workspace-write`; do not use it for read-only checks.

## Transport and evidence

`--transport cli` uses `codex exec`; `--transport app-server` uses a JSON-RPC
Codex app-server session. Pick one explicitly. The adapter performs the live
app-server catalog check and then runs exactly the requested transport—there is
no fallback from one transport to the other.

Each result is one normalized JSON envelope with the selected
model/effort/transport/sandbox, terminal IDs/evidence, nested-dispatch state,
and provider usage when it was observed. Usage is otherwise `"unknown"`.
The adapter never invents a dollar cost, and it makes no Kimi runner receipt or
production-acceptance claim.

It refuses a dispatch started beneath `PROCTOR_CHILD`. It also refuses a result
when it observes nested Codex activity (`collabAgentToolCall` or
`subAgentActivity`), even if a turn otherwise completed.

Resume is deliberately constrained: `--resume-identity` is available only for
`app-server`, where the adapter reapplies cwd and sandbox binding. CLI resume
is refused because the installed CLI cannot reassert that cwd/sandbox binding.

## Explicit-destination copy

The separate installer has no default directory and never performs a durable
installation on its own. Supply the destination deliberately:

```powershell
python harnesses/codex/delegate/install.py --destination C:\chosen\codex-delegate
```

It copies only `delegate.py`, `catalog.py`, this README, and the tracked config
example. It never copies a live local config, credentials, or a machine-specific
Codex executable path. A destination is allowed to be temporary; verify that
choice before using a durable location.
