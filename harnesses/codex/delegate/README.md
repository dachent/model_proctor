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
`app-server` `model/list` data before dispatch. It follows every catalog page;
a catalog mismatch or invalid pagination cursor refuses dispatch.

From the source checkout root:

```powershell
python harnesses/codex/delegate/delegate.py `
  --preset terra --config C:\safe-local\codex-local-config.json `
  --transport cli --codex-executable C:\path\to\codex.exe `
  --workspace C:\work\checkout --task-file C:\safe-local\task.txt
```

Supply exactly one task source. An external `--task-file` must resolve outside
`--workspace`; alternatively, `--task-stdin` reads exact UTF-8 bytes from
standard input without any task-file staging. The default sandbox is truly
`read-only`. `--write` explicitly changes it to `workspace-write`; do not use
it for read-only checks.

## Transport and evidence

`--transport cli` uses `codex exec`; `--transport app-server` uses a JSON-RPC
Codex app-server session. Pick one explicitly. The adapter performs the live
app-server catalog check and then runs exactly the requested transport—there is
no fallback from one transport to the other.

`--timeout SECONDS` sets one deadline shared by catalog preflight and the selected
transport. It defaults to `1800` seconds and must be finite, positive, and no more
than `7200`. The option belongs to the adapter and does not change the Codex child
arguments. A timeout returns `operational_failure` with error class
`TransportTimeout`; process cleanup uses at most two additional one-second waits.

Each result is one normalized JSON envelope with the selected
model/effort/transport/sandbox, terminal IDs/evidence, nested-dispatch state,
provider usage when it was observed, and the selected worker's final
`agent_message` when the protocol exposes one. `agent_message` is bound to the
thread and turn IDs returned directly by app-server; replayed item IDs are
deduplicated. It is `null` if no final message was observed. Usage is otherwise
`"unknown"`.
The adapter never invents a dollar cost, and it makes no Kimi runner receipt or
production-acceptance claim.

Parsed IDs, terminal evidence, and observed nesting survive later stream or
cleanup failures. CLI success requires a zero child exit code. App-server success
requires a terminal notification matching the returned thread and turn IDs and a
reaped session; an RPC error stops the lifecycle.

It refuses a dispatch whenever the `PROCTOR_CHILD` environment key is present,
including an empty value. It also refuses a result
when it observes nested Codex activity (`collabAgentToolCall`,
`collabToolCall`, `subAgentActivity`, or their protocol aliases), even if a
turn otherwise completed. Notifications are evidence only: direct
`thread/start` and `turn/start` responses establish the selected app-server
identity.

Resume is deliberately constrained: `--resume-identity` is available only for
`app-server`, where the adapter reapplies cwd and sandbox binding. CLI resume
is refused because the installed CLI cannot reassert that cwd/sandbox binding.

## Explicit-destination copy

The separate installer has no default directory and never performs a durable
installation on its own. Choose a fresh destination with no manifest-name
collisions and supply it deliberately:

```powershell
python harnesses/codex/delegate/install.py --destination C:\chosen\codex-delegate
```

The exact flat installed boundary is `delegate.py`, `catalog.py`,
`runner_delegate.py`, `runner-agent-map.json`, `local-config.example.json`, and
`README.md`. The bridge and map are installed beside the delegate, catalog, and
tracked config example so the Codex adapter can serve the existing runner
contract. The shared Kimi control-plane files
`C:\Tools\model-proctor\runner.py` and
`C:\Tools\model-proctor\task_schema.py` remain required; the Codex installer
does not copy or rewrite them. It never copies a live local config, credentials,
or a machine-specific Codex executable path. A destination is allowed to be
temporary; verify that choice before using a durable location. If any of those
six filenames already exists at the destination, installation fails before
copying anything. There is no default destination and no force option.

For runner-gated bridge use,
`C:\Tools\model-proctor\codex-delegate\local-config.json` is required beside
`runner_delegate.py`; the bridge accepts no arbitrary config path. Arbitrary
`--config` paths are for direct `delegate.py` dispatch only.

From a flat installed directory, invoke its local `delegate.py`:

```powershell
Set-Location C:\chosen\codex-delegate
python .\delegate.py `
  --preset terra --config C:\safe-local\codex-local-config.json `
  --transport cli --codex-executable C:\path\to\codex.exe `
  --workspace C:\work\checkout --task-file C:\safe-local\task.txt
```

## Standalone desktop skill

The canonical source for the visible Codex trigger is
[`../skill/model-proctor/`](../skill/model-proctor/). It is intentionally only
a standalone `SKILL.md` plus a small dispatcher: no MCP server, daemon, plugin,
or second desktop UI is required. After a user-authorized installation into the
local Codex skills root, invoke it in the desktop composer as
`$model-proctor terra: <task>` (or `luna` / `astra`).

The dispatcher pipes its UTF-8 task directly to the adapter's `--task-stdin`;
it does not stage a task file. It resolves a desktop-bundled `codex.exe` below
LocalAppData and never falls back to an unrelated `PATH` CLI. It preserves the
installed, untracked `local-config.json`; the source installer remains
collision-safe and therefore is not an in-place upgrade mechanism.
