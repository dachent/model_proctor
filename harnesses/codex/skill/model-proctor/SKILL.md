---
name: model-proctor
description: Use when the user explicitly asks to delegate or proctor a coding task with Luna, Terra, or Astra, or invokes model-proctor. Do not use for ordinary direct work or automatic model routing.
---

# Model Proctor for Codex

Use this skill as an explicit handoff to one selected Codex worker. It does not
choose a model from task shape.

## Select the preset

Use the preset the user names:

| User selection | Model and effort |
| --- | --- |
| Luna | `gpt-5.6-luna`, `medium` |
| Terra | `gpt-5.6-terra`, `high` |
| Astra | `gpt-6-astra`, `max` |

If the user invokes `$model-proctor` without naming Luna, Terra, or Astra, ask
which preset they want. Do not infer one.

## Delegate

1. Keep the requested task text intact. Use the active repository root as the
   workspace; if there is no unambiguous workspace, ask for it.
2. Encode the exact requested task as one UTF-8 Base64 value before putting it
   in a shell command. Do not embed the raw task in a PowerShell here-string or
   other shell literal. The helper decodes it once and passes its UTF-8 bytes
   straight to the adapter's standard input, resolves the desktop-bundled Codex
   executable, and invokes the installed adapter with `app-server` transport.
   Do not use a bare `codex` command, a daemon, MCP, another UI, or a task file.

   ```powershell
$dispatcher = Join-Path $env:USERPROFILE '.codex\skills\model-proctor\scripts\dispatch.py'
$taskBase64 = '<Base64 of the exact requested task, encoded as UTF-8>'
$taskBase64 | & python $dispatcher --task-base64-stdin `
    --preset <luna|terra|astra> --workspace '<absolute workspace path>'
   ```

3. Read-only is the default. Add `--write` only when the user explicitly
   authorizes workspace changes. Never remove or overwrite `PROCTOR_CHILD`. Do
   not put secrets, credential values, or environment dumps into the task: the
   read-only sandbox is not credential isolation.
4. Return the normalized result, including its selected model, effort, sandbox,
   nested-dispatch status, and `agent_message`. A completed transport envelope
   is not independent acceptance of the worker's work. A detected native
   subagent activity is refused after it is observed.

The reliable visible invocation is, for example:

```text
$model-proctor astra: Adversarially review this repository's authentication changes.
```
