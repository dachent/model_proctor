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
        return value.get("type") in {"collabAgentToolCall", "subAgentActivity"} or any(
                   key in {"collabAgentToolCall", "subAgentActivity"} or _nested(item)
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


def _terminal_status(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("type", ""))
    if event_type.startswith("turn."):
        return event_type.rsplit(".", 1)[-1]
    return event["params"]["turn"]["status"]


def _usage(events: list[Mapping[str, Any]]) -> Any:
    observed: Any = "unknown"
    for event in events:
        params = event.get("params")
        if event.get("method") == "thread/tokenUsage/updated" and isinstance(params, dict):
            if params.get("tokenUsage") is not None:
                observed = params["tokenUsage"]
        for parent in (event, event.get("params"),
                       event.get("params", {}).get("turn") if isinstance(event.get("params"), dict) else None):
            if isinstance(parent, dict) and parent.get("usage") is not None:
                observed = parent["usage"]
    return observed


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
    proc = process(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                   cwd=cwd, env=dict(env))
    if proc.stdin is None or proc.stdout is None:
        raise ValueError("CLI transport did not provide stdio streams")
    proc.stdin.write(input)
    proc.stdin.flush()
    try:
        proc.stdin.close()
    except Exception:
        pass
    stdout = getattr(proc, "stdout", b"")
    if hasattr(stdout, "read"):
        stdout = stdout.read()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=1)
    return _json_lines(stdout), proc


def _catalog_rpc(executable: str, process: Callable[..., Any], env: Mapping[str, str]) -> tuple[Mapping[str, Any], list[dict[str, Any]]]:
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "model-proctor", "version": "1"}, "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "model/list", "params": {}},
    ]
    events = _rpc(process, [executable, "app-server", "--stdio"], requests, cwd=None, env=env)
    for event in events:
        if event.get("id") == 2 and isinstance(event.get("result"), dict):
            return event["result"], events
    raise ValueError("app-server model/list did not return a catalog")


class _RpcSession:
    """Popen-compatible JSON-RPC conversation that tolerates notifications."""
    def __init__(self, process: Callable[..., Any], argv: list[str], *, cwd: Optional[str], env: Mapping[str, str]):
        self.proc = process(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            cwd=cwd, env=dict(env))
        if self.proc.stdin is None or self.proc.stdout is None:
            raise ValueError("app-server did not provide stdio streams")
        self.events: list[dict[str, Any]] = []

    def send(self, request: dict[str, Any]) -> None:
        self.proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def read_response(self, required_id: int) -> dict[str, Any]:
        while True:
            raw = self.proc.stdout.readline()
            if not raw:
                raise ValueError("app-server ended before required response")
            event = self._record(raw)
            if "method" in event:  # notifications may legally interleave RPC responses.
                continue
            if event.get("id") != required_id:
                raise ValueError("app-server response order was invalid")
            return event

    def read_terminal(self) -> list[dict[str, Any]]:
        while True:
            raw = self.proc.stdout.readline()
            if not raw:
                break
            event = self._record(raw)
            if _terminal_event(event):
                return [item for item in self.events if _terminal_event(item)]
        raise ValueError("app-server transport did not expose a terminal turn event")

    def _record(self, raw: bytes) -> dict[str, Any]:
        event = _json_lines(raw)[0]
        self.events.append(event)
        if "method" in event and "id" in event:
            self.send({"jsonrpc": "2.0", "id": event["id"], "error": {
                "code": -32000, "message": "server request refused by bounded delegate"}})
            raise ValueError("app-server server-to-client request refused")
        return event

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                self.proc.kill()
                self.proc.wait(timeout=1)
            except Exception:
                pass
        except Exception:
            pass


def _rpc(process: Callable[..., Any], argv: list[str], requests: list[dict[str, Any]], *,
         cwd: Optional[str], env: Mapping[str, str]) -> list[dict[str, Any]]:
    session = _RpcSession(process, argv, cwd=cwd, env=env)
    try:
        session.send(requests[0]); session.read_response(1)
        session.send(requests[1]); session.send(requests[2]); session.read_response(2)
        return session.events
    finally:
        session.close()


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
    if resume is not None:
        raise ValueError("CLI resume is refused: installed resume cannot reapply sandbox/cwd binding")
    argv = [executable, "exec", "-m", selection.model, "-c", f"model_reasoning_effort={selection.effort}",
            "-s", sandbox, "-C", str(workspace), "--json", "-"]
    events, _ = _run(process, argv, input=task.encode("utf-8"), cwd=str(workspace), env=env)
    terminal = [event for event in events if _terminal_event(event)]
    if not terminal:
        raise ValueError("CLI transport did not expose a terminal turn event")
    last = terminal[-1]
    status = _terminal_status(last)
    return _result(status, model=selection.model, effort=selection.effort, transport="cli", sandbox=sandbox,
                   session_id=next((_id_from(event, "session_id", "thread_id") for event in events
                                    if _id_from(event, "session_id", "thread_id")), None),
                   turn_id=_id_from(last, "turn_id"),
                   terminal_evidence=terminal, nested_dispatch_detected=any(_nested(event) for event in events),
                   usage=_usage(events))


def _app_server(executable: str, selection: Any, task: str, workspace: Path, sandbox: str,
                resume: Optional[str], catalog_events: list[dict[str, Any]], process: Callable[..., Any],
                env: Mapping[str, str]) -> dict[str, Any]:
    policy = {"type": "workspaceWrite" if sandbox == "workspace-write" else "readOnly", "networkAccess": False}
    session = _RpcSession(process, [executable, "app-server", "--stdio"], cwd=str(workspace), env=env)
    try:
        session.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "model-proctor", "version": "1"}, "capabilities": {}}})
        session.read_response(1)
        session.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        session.send({"jsonrpc": "2.0", "id": 2, "method": "model/list", "params": {}})
        live_catalog = session.read_response(2).get("result")
        # Bind this exact process session to the already-selected identity before any thread work.
        live = resolve_selection(parse_catalog(live_catalog), _empty_config(),
                                 model=selection.model, effort=selection.effort)
        thread_method = "thread/resume" if resume else "thread/start"
        thread_params = {"cwd": str(workspace), "model": live.model, "sandbox": sandbox}
        if resume:
            thread_params["threadId"] = resume
        session.send({"jsonrpc": "2.0", "id": 3, "method": thread_method, "params": thread_params})
        thread_response = session.read_response(3)
        thread_id = _id_from(thread_response, "thread_id", "threadId", "id")
        if thread_id is None:
            raise ValueError("app-server thread response lacked a thread id")
        session.send({"jsonrpc": "2.0", "id": 4, "method": "turn/start", "params": {
            "threadId": thread_id, "model": live.model, "effort": live.effort,
            "input": [{"type": "text", "text": task}], "sandboxPolicy": policy}})
        session.read_response(4)
        terminal = session.read_terminal()
        last = terminal[-1]
        status = _terminal_status(last)
        return _result(status, model=live.model, effort=live.effort, transport="app-server", sandbox=sandbox,
                       thread_id=thread_id, session_id=thread_id, turn_id=_id_from(last, "turn_id", "turnId", "id"),
                       terminal_evidence=terminal, nested_dispatch_detected=any(_nested(event) for event in session.events),
                       usage=_usage(session.events))
    finally:
        session.close()


def run_delegate(args: argparse.Namespace, *, catalog_payload: Optional[Mapping[str, Any]] = None,
                 popen_factory: Callable[..., Any] = subprocess.Popen,
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
        if args.transport == "cli" and args.resume_identity is not None:
            raise ValueError("CLI resume is refused: installed resume cannot reapply sandbox/cwd binding")
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
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return _result("operational_failure", model=selection.model, effort=selection.effort,
                       transport=args.transport, sandbox=sandbox, error=str(exc)), EXIT_OPERATIONAL
    if result["nested_dispatch_detected"]:
        result["status"] = "refused"; result["error"] = "nested Codex activity observed; result refused"
        return result, EXIT_OPERATIONAL
    return result, EXIT_OK if result["status"] == "completed" else EXIT_OPERATIONAL


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
