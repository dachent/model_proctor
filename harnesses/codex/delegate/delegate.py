#!/usr/bin/env python3
"""Explicit, catalog-bound transports for one Codex dispatch.

This module deliberately owns transport mechanics only.  Model and preset
validation remains in :mod:`catalog`; no routing or acceptance policy lives
here.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from catalog import CatalogContractError, LocalConfig, load_local_config, parse_catalog, resolve_selection

EXIT_OK = 0
EXIT_INVALID = 64
EXIT_OPERATIONAL = 70


def _result(status: str, *, model: Optional[str] = None, effort: Optional[str] = None,
            transport: Optional[str] = None, sandbox: Optional[str] = None,
            error: Optional[str] = None, **extra: Any) -> dict[str, Any]:
    result = {"schema_version": 1, "status": status, "model": model, "effort": effort,
              "transport": transport, "sandbox": sandbox, "session_id": None,
              "thread_id": None, "turn_id": None, "terminal_evidence": [],
              "nested_dispatch_detected": False, "usage": "unknown", "error": error}
    result.update(extra)
    return result


def _empty_config() -> LocalConfig:
    return LocalConfig(MappingProxyType({}))


def _outside_workspace(task: str, workspace: Path) -> str:
    path = Path(task).resolve()
    try:
        path.relative_to(workspace)
    except ValueError:
        pass
    else:
        raise ValueError("task file must resolve outside the target workspace")
    return path.read_text(encoding="utf-8")


def _load_config(path: Optional[str], preset: Optional[str]) -> LocalConfig:
    if path is None:
        if preset is not None:
            raise ValueError("--preset requires --config pointing to an untracked local schema-1 file")
        return _empty_config()
    return load_local_config(path)


def _nested(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key in {"collabAgentToolCall", "subAgentActivity"} or _nested(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_nested(item) for item in value)
    return False


def _terminal_event(event: Mapping[str, Any]) -> bool:
    event_type = str(event.get("type", ""))
    if event_type.startswith("turn.") and event_type.rsplit(".", 1)[-1] in {"completed", "failed", "interrupted"}:
        return True
    params = event.get("params")
    if isinstance(params, dict):
        turn = params.get("turn")
        return isinstance(turn, dict) and turn.get("status") in {"completed", "failed", "interrupted"}
    return False


def _id_from(event: Mapping[str, Any], *names: str) -> Optional[str]:
    for name in names:
        value = event.get(name)
        if isinstance(value, str) and value:
            return value
    for container_name in ("params", "result"):
        params = event.get(container_name)
        if isinstance(params, dict):
            for parent in (params, params.get("thread"), params.get("turn")):
                if isinstance(parent, dict):
                    for name in names:
                        value = parent.get(name)
                        if isinstance(value, str) and value:
                            return value
    return None


def _json_lines(raw: bytes | str) -> list[dict[str, Any]]:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    events = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("malformed JSONL from Codex transport") from exc
        if not isinstance(value, dict):
            raise ValueError("Codex transport JSONL event must be an object")
        events.append(value)
    return events


def _run(process: Callable[..., Any], argv: list[str], *, input: bytes, cwd: Optional[str], env: Mapping[str, str]) -> tuple[list[dict[str, Any]], Any]:
    proc = process(argv, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   cwd=cwd, env=dict(env))
    stdout = getattr(proc, "stdout", b"")
    if hasattr(stdout, "read"):
        stdout = stdout.read()
    return _json_lines(stdout), proc


def _catalog_rpc(executable: str, process: Callable[..., Any], env: Mapping[str, str]) -> tuple[Mapping[str, Any], list[dict[str, Any]]]:
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "model/list", "params": {}},
    ]
    events = _rpc(process, [executable, "app-server", "--stdio"], requests, cwd=None, env=env)
    for event in events:
        if event.get("id") == 2 and isinstance(event.get("result"), dict):
            return event["result"], events
    raise ValueError("app-server model/list did not return a catalog")


def _rpc(process: Callable[..., Any], argv: list[str], requests: list[dict[str, Any]], *,
         cwd: Optional[str], env: Mapping[str, str]) -> list[dict[str, Any]]:
    """Run JSON-RPC in lifecycle order; never issue turn work before catalog proof."""
    proc = process(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   cwd=cwd, env=dict(env))
    if proc.stdin is None or proc.stdout is None:
        raise ValueError("app-server did not provide stdio streams")
    events: list[dict[str, Any]] = []
    def send(request: dict[str, Any]) -> None:
        proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        proc.stdin.flush()
    def read_one(required_id: Optional[int] = None) -> dict[str, Any]:
        raw = proc.stdout.readline()
        if not raw:
            raise ValueError("app-server ended before required response")
        event = _json_lines(raw)[0]
        events.append(event)
        if required_id is not None and event.get("id") != required_id:
            raise ValueError("app-server response order was invalid")
        return event
    send(requests[0]); read_one(1)
    send(requests[1])
    send(requests[2]); read_one(2)
    for request in requests[3:]:
        send(request)
        if request.get("id") == 3:
            read_one(3)
        elif request.get("id") == 4:
            read_one(4)
    while True:
        raw = proc.stdout.readline()
        if not raw:
            break
        events.extend(_json_lines(raw))
        if _terminal_event(events[-1]):
            break
    try:
        proc.wait()
    except Exception:
        pass
    return events


def _identity(raw: Optional[str], *, transport: str, model: str, effort: str, sandbox: str) -> Optional[str]:
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("resume identity must be a normalized JSON record or identity object") from exc
    if isinstance(record, dict) and isinstance(record.get("identity"), dict):
        record = record["identity"]
    if not isinstance(record, dict):
        raise ValueError("resume identity must be an object")
    if any(record.get(key) != expected for key, expected in (("transport", transport), ("model", model),
                                                               ("effort", effort), ("sandbox", sandbox))):
        raise ValueError("resume identity does not bind the requested transport/model/effort/sandbox")
    name = "session_id" if transport == "cli" else "thread_id"
    ident = record.get(name)
    if not isinstance(ident, str) or not ident:
        raise ValueError(f"resume identity lacks bound {name}")
    return ident


def _cli(executable: str, selection: Any, task: str, workspace: Path, sandbox: str,
         resume: Optional[str], process: Callable[..., Any], env: Mapping[str, str]) -> dict[str, Any]:
    if resume is None:
        argv = [executable, "exec", "-m", selection.model, "-c", f"model_reasoning_effort={selection.effort}",
                "-s", sandbox, "-C", str(workspace), "--json", "-"]
    else:
        # The installed resume protocol intentionally has no sandbox/cwd override.
        argv = [executable, "exec", "resume", resume, "-m", selection.model,
                "-c", f"model_reasoning_effort={selection.effort}", "--json", "-"]
    events, _ = _run(process, argv, input=task.encode("utf-8"), cwd=str(workspace), env=env)
    terminal = [event for event in events if _terminal_event(event)]
    if not terminal:
        raise ValueError("CLI transport did not expose a terminal turn event")
    last = terminal[-1]
    return _result("completed", model=selection.model, effort=selection.effort, transport="cli", sandbox=sandbox,
                   session_id=next((_id_from(event, "session_id", "thread_id") for event in events
                                    if _id_from(event, "session_id", "thread_id")), None),
                   turn_id=_id_from(last, "turn_id"),
                   terminal_evidence=terminal, nested_dispatch_detected=any(_nested(event) for event in events))


def _app_server(executable: str, selection: Any, task: str, workspace: Path, sandbox: str,
                resume: Optional[str], catalog_events: list[dict[str, Any]], process: Callable[..., Any],
                env: Mapping[str, str]) -> dict[str, Any]:
    thread_method = "thread/resume" if resume else "thread/start"
    thread_params = {"threadId": resume} if resume else {"cwd": str(workspace), "sandbox": sandbox}
    policy = {"type": "workspaceWrite" if sandbox == "workspace-write" else "readOnly", "networkAccess": False}
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "model/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": thread_method, "params": thread_params},
        {"jsonrpc": "2.0", "id": 4, "method": "turn/start", "params": {
            "threadId": resume or "__from_thread_start__", "input": [{"type": "text", "text": task}],
            "sandboxPolicy": policy}},
    ]
    events = _rpc(process, [executable, "app-server", "--stdio"], requests, cwd=str(workspace), env=env)
    # A live response is mandatory; catalog_events exists only to retain terminal evidence from preflight callers.
    if not any(event.get("id") == 2 and isinstance(event.get("result"), dict) for event in events):
        raise ValueError("app-server model/list did not return a catalog")
    thread_id = resume
    for event in events:
        if event.get("id") == 3:
            thread_id = _id_from(event, "thread_id", "threadId", "id") or thread_id
    terminal = [event for event in events if _terminal_event(event)]
    if not terminal:
        raise ValueError("app-server transport did not expose a terminal turn event")
    last = terminal[-1]
    return _result("completed", model=selection.model, effort=selection.effort, transport="app-server", sandbox=sandbox,
                   thread_id=thread_id, session_id=thread_id, turn_id=_id_from(last, "turn_id", "turnId", "id"),
                   terminal_evidence=terminal, nested_dispatch_detected=any(_nested(event) for event in events))


def run_delegate(args: argparse.Namespace, *, catalog_payload: Optional[Mapping[str, Any]] = None,
                 popen_factory: Callable[..., Any] = subprocess.run,
                 environ: Optional[Mapping[str, str]] = None) -> tuple[dict[str, Any], int]:
    """Perform exactly the named transport; test callers may supply a catalog fixture."""
    env = dict(os.environ if environ is None else environ)
    sandbox = "workspace-write" if args.write else "read-only"
    if env.get("PROCTOR_CHILD"):
        return _result("refused", transport=args.transport, sandbox=sandbox, error="PROCTOR_CHILD parent refusal"), EXIT_INVALID
    child_env = dict(env); child_env["PROCTOR_CHILD"] = "1"
    try:
        workspace = Path(args.workspace).resolve()
        if not workspace.is_dir(): raise ValueError("workspace must be an existing directory")
        task = _outside_workspace(args.task_file, workspace)
        config = _load_config(args.config, args.preset)
        if catalog_payload is None:
            catalog_payload, catalog_events = _catalog_rpc(args.codex_executable, popen_factory, child_env)
        else:
            catalog_events = []
        selection = resolve_selection(parse_catalog(catalog_payload), config, model=args.model, preset=args.preset, effort=args.effort)
        resume = _identity(args.resume_identity, transport=args.transport, model=selection.model,
                           effort=selection.effort, sandbox=sandbox)
    except (OSError, UnicodeError, ValueError, CatalogContractError) as exc:
        return _result("invalid", transport=args.transport, sandbox=sandbox, error=str(exc)), EXIT_INVALID
    try:
        if args.transport == "cli":
            result = _cli(args.codex_executable, selection, task, workspace, sandbox, resume, popen_factory, child_env)
        elif args.transport == "app-server":
            # app-server's own model/list response is required and validated before thread work.
            result = _app_server(args.codex_executable, selection, task, workspace, sandbox, resume, catalog_events, popen_factory, child_env)
        else:
            raise ValueError("transport must be cli or app-server")
    except (OSError, ValueError) as exc:
        return _result("operational_failure", model=selection.model, effort=selection.effort,
                       transport=args.transport, sandbox=sandbox, error=str(exc)), EXIT_OPERATIONAL
    if result["nested_dispatch_detected"]:
        result["status"] = "refused"; result["error"] = "nested Codex activity observed; result refused"
        return result, EXIT_OPERATIONAL
    return result, EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delegate")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--model"); group.add_argument("--preset")
    parser.add_argument("--transport", choices=("cli", "app-server"), required=True)
    parser.add_argument("--task-file", required=True); parser.add_argument("--workspace", required=True)
    parser.add_argument("--write", action="store_true"); parser.add_argument("--effort")
    parser.add_argument("--codex-executable", default="codex"); parser.add_argument("--config")
    parser.add_argument("--resume-identity")
    return parser


def main() -> None:
    parser = build_parser()
    try:
        args = parser.parse_args()
        result, code = run_delegate(args)
    except SystemExit:
        raise
    except Exception as exc:  # stdout is still one normalized operational envelope.
        result, code = _result("operational_failure", error=type(exc).__name__), EXIT_OPERATIONAL
    print(json.dumps(result, separators=(",", ":")))
    raise SystemExit(code)


if __name__ == "__main__": main()
