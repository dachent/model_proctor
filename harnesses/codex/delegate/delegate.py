#!/usr/bin/env python3
"""Explicit, catalog-bound transports for one Codex dispatch.

This module deliberately owns transport mechanics only.  Model and preset
validation remains in :mod:`catalog`; no routing or acceptance policy lives
here.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from catalog import CatalogContractError, LocalConfig, load_local_config, parse_catalog, resolve_selection

EXIT_OK = 0
EXIT_INVALID = 64
EXIT_OPERATIONAL = 70


class TransportTimeout(TimeoutError):
    """The single adapter deadline expired."""


class CleanupFailure(RuntimeError):
    """The child could not be reaped after bounded cleanup."""


class RpcFailure(RuntimeError):
    """The server returned a JSON-RPC error."""


class ChildExitFailure(RuntimeError):
    """The CLI process exited unsuccessfully."""


class _Deadline:
    def __init__(self, seconds: float):
        if not math.isfinite(seconds) or not 0 < seconds <= 7200:
            raise ValueError("--timeout must be finite, positive, and at most 7200 seconds")
        self.end = time.monotonic() + seconds

    def remaining(self) -> float:
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            raise TransportTimeout()
        return remaining

    def call(self, operation: Callable[[], Any]) -> Any:
        """Bound Windows pipe reads/writes even when the operation never returns."""
        self.remaining()
        completed: queue.Queue[Any] = queue.Queue()

        def work() -> None:
            try:
                completed.put((True, operation()))
            except Exception as exc:
                completed.put((False, exc))

        threading.Thread(target=work, daemon=True).start()
        try:
            ok, value = completed.get(timeout=self.remaining())
        except queue.Empty as exc:
            raise TransportTimeout() from exc
        self.remaining()
        if not ok:
            raise value
        return value


def _close_pipe(pipe: Any) -> None:
    # A blocked writer/reader may own the stream lock; never close it on the caller.
    def close() -> None:
        try:
            if pipe is not None:
                pipe.close()
        except Exception:
            pass
    threading.Thread(target=close, daemon=True).start()


def _cleanup(proc: Any) -> int:
    active_error = sys.exc_info()[1]
    _close_pipe(proc.stdin)
    try:
        try:
            code = proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait(timeout=1)
        if not isinstance(code, int):
            raise CleanupFailure()
        return code
    except Exception as exc:
        if isinstance(active_error, TransportTimeout):
            raise active_error from exc
        raise CleanupFailure() from exc
    finally:
        _close_pipe(proc.stdout)


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


def _nested(event: Mapping[str, Any]) -> bool:
    # Only protocol event/item nodes have Codex discriminators. Tool payloads
    # (including MCP arguments and structuredContent) are opaque application data.
    containers = [event]
    for name in ("params", "result"):
        value = event.get(name)
        if isinstance(value, dict):
            containers.append(value)
    for container in list(containers):
        turn = container.get("turn")
        if isinstance(turn, dict):
            containers.append(turn)
        thread = container.get("thread")
        turns = thread.get("turns") if isinstance(thread, dict) else None
        if isinstance(turns, list):
            containers.extend(turn for turn in turns if isinstance(turn, dict))
    nodes = [event]
    for container in containers:
        item = container.get("item")
        if isinstance(item, dict):
            nodes.append(item)
        items = container.get("items")
        if isinstance(items, list):
            nodes.extend(item for item in items if isinstance(item, dict))
    for node in nodes:
        if "type" in node and not isinstance(node["type"], str):
            raise ValueError("Codex event type must be a string")
        if (node.get("type") in ("collabAgentToolCall", "subAgentActivity")
                or any(name in node for name in ("collabAgentToolCall", "subAgentActivity"))):
            return True
    return False


def _terminal_event(event: Mapping[str, Any]) -> bool:
    event_type = event.get("type", "")
    if not isinstance(event_type, str):
        raise ValueError("Codex event type must be a string")
    if event_type.startswith("turn.") and event_type.rsplit(".", 1)[-1] in {"completed", "failed", "interrupted"}:
        return True
    params = event.get("params")
    if isinstance(params, dict):
        turn = params.get("turn")
        if isinstance(turn, dict):
            status = turn.get("status")
            if status is not None and not isinstance(status, str):
                raise ValueError("Codex turn status must be a string")
            return status in ("completed", "failed", "interrupted")
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


def _direct_id(value: Any, name: str) -> Optional[str]:
    identity = value.get(name) if isinstance(value, dict) else None
    return identity if isinstance(identity, str) and identity else None


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


def _observe(event: dict[str, Any], result: dict[str, Any]) -> None:
    """Retain safely parsed observations before a later stream or cleanup failure."""
    if result["transport"] == "cli":
        result["session_id"] = result["session_id"] or _id_from(event, "session_id", "thread_id")
        result["turn_id"] = _id_from(event, "turn_id") or result["turn_id"]
    else:
        params = event.get("params")
        if (result["thread_id"] is not None and isinstance(params, dict)
                and params.get("threadId") == result["thread_id"]):
            # Until turn/start responds, a matching-thread notification is still evidence.
            turn = params.get("turn")
            result["turn_id"] = (result["turn_id"] or _direct_id(turn, "id")
                                 or _direct_id(params, "turn_id") or _direct_id(params, "turnId"))
    if "method" in event and not isinstance(event["method"], str):
        raise ValueError("Codex RPC method must be a string")
    if _terminal_event(event):
        result["terminal_evidence"].append(event)
    result["nested_dispatch_detected"] |= _nested(event)
    observed = _usage([event])
    if observed != "unknown":
        result["usage"] = observed


def _run(process: Callable[..., Any], argv: list[str], *, input: bytes, cwd: Optional[str],
         env: Mapping[str, str], deadline: _Deadline, result: dict[str, Any]) -> None:
    deadline.remaining()
    proc = process(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                   cwd=cwd, env=dict(env))
    try:
        if proc.stdin is None or proc.stdout is None:
            raise ValueError("CLI transport did not provide stdio streams")
        def send() -> None:
            proc.stdin.write(input)
            proc.stdin.flush()
            proc.stdin.close()
        deadline.call(send)
        while True:
            raw = deadline.call(proc.stdout.readline)
            if not raw:
                break
            for event in _json_lines(raw):
                _observe(event, result)
    finally:
        code = _cleanup(proc)
    if code != 0:
        raise ChildExitFailure()


def _catalog_rpc(executable: str, process: Callable[..., Any], env: Mapping[str, str],
                 deadline: _Deadline, result: dict[str, Any]) -> Mapping[str, Any]:
    session = _RpcSession(process, [executable, "app-server", "--stdio"], cwd=None,
                          env=env, deadline=deadline, result=result)
    try:
        session.initialize()
        return session.catalog()
    finally:
        session.close()


class _RpcSession:
    """Popen-compatible JSON-RPC conversation that tolerates notifications."""
    def __init__(self, process: Callable[..., Any], argv: list[str], *, cwd: Optional[str],
                 env: Mapping[str, str], deadline: _Deadline, result: dict[str, Any]):
        deadline.remaining()
        self.proc = process(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            cwd=cwd, env=dict(env))
        if self.proc.stdin is None or self.proc.stdout is None:
            _cleanup(self.proc)
            raise ValueError("app-server did not provide stdio streams")
        self.events: list[dict[str, Any]] = []
        self.next_id = 1
        self.deadline = deadline
        self.result = result

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return self.read_response(request_id)

    def initialize(self) -> None:
        self.request("initialize", {
            "clientInfo": {"name": "model-proctor", "version": "1"}, "capabilities": {}})
        self.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})

    def catalog(self) -> dict[str, Any]:
        data: list[Any] = []
        seen: set[str] = set()
        params: dict[str, Any] = {}
        while True:
            page = self.request("model/list", params).get("result")
            if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                raise ValueError("app-server model/list did not return catalog data")
            data.extend(page["data"])
            cursor = page.get("nextCursor")
            if cursor is None:
                return {"data": data}
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ValueError("app-server model/list returned an invalid or repeated cursor")
            seen.add(cursor)
            params = {"cursor": cursor}

    def send(self, request: dict[str, Any]) -> None:
        def write() -> None:
            self.proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        self.deadline.call(write)

    def read_response(self, required_id: int) -> dict[str, Any]:
        while True:
            raw = self.deadline.call(self.proc.stdout.readline)
            if not raw:
                raise ValueError("app-server ended before required response")
            event = self._record(raw)
            if "method" in event:  # notifications may legally interleave RPC responses.
                continue
            if event.get("id") != required_id:
                raise ValueError("app-server response order was invalid")
            return event

    def read_terminal(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        def matches(event: dict[str, Any]) -> bool:
            params = event.get("params")
            turn = params.get("turn") if isinstance(params, dict) else None
            return ("method" in event and isinstance(params, dict)
                    and params.get("threadId") == thread_id
                    and _direct_id(turn, "id") == turn_id
                    and _terminal_event(event))
        for event in self.events:
            if matches(event):
                return event
        while True:
            raw = self.deadline.call(self.proc.stdout.readline)
            if not raw:
                break
            event = self._record(raw)
            if matches(event):
                return event
        raise ValueError("app-server transport did not expose a terminal turn event")

    def _record(self, raw: bytes) -> dict[str, Any]:
        event = _json_lines(raw)[0]
        self.events.append(event)
        _observe(event, self.result)
        if "method" in event and "id" in event:
            self.send({"jsonrpc": "2.0", "id": event["id"], "error": {
                "code": -32000, "message": "server request refused by bounded delegate"}})
            raise ValueError("app-server server-to-client request refused")
        if "method" not in event and "error" in event:
            raise RpcFailure()
        return event

    def close(self) -> None:
        _cleanup(self.proc)


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
         resume: Optional[str], process: Callable[..., Any], env: Mapping[str, str],
         deadline: _Deadline, result: dict[str, Any]) -> None:
    if resume is not None:
        raise ValueError("CLI resume is refused: installed resume cannot reapply sandbox/cwd binding")
    argv = [executable, "exec", "-m", selection.model, "-c", f"model_reasoning_effort={selection.effort}",
            "-s", sandbox, "-C", str(workspace), "--json", "-"]
    before = len(result["terminal_evidence"])
    _run(process, argv, input=task.encode("utf-8"), cwd=str(workspace), env=env,
         deadline=deadline, result=result)
    terminal = result["terminal_evidence"][before:]
    if not terminal:
        raise ValueError("CLI transport did not expose a terminal turn event")
    result["status"] = _terminal_status(terminal[-1])


def _app_server(executable: str, selection: Any, task: str, workspace: Path, sandbox: str,
                resume: Optional[str], process: Callable[..., Any], env: Mapping[str, str],
                deadline: _Deadline, result: dict[str, Any]) -> None:
    policy = {"type": "workspaceWrite" if sandbox == "workspace-write" else "readOnly", "networkAccess": False}
    session = _RpcSession(process, [executable, "app-server", "--stdio"], cwd=str(workspace),
                          env=env, deadline=deadline, result=result)
    try:
        session.initialize()
        live_catalog = session.catalog()
        # Bind this exact process session to the already-selected identity before any thread work.
        live = resolve_selection(parse_catalog(live_catalog), _empty_config(),
                                 model=selection.model, effort=selection.effort)
        thread_method = "thread/resume" if resume else "thread/start"
        thread_params = {"cwd": str(workspace), "model": live.model, "sandbox": sandbox}
        if resume:
            thread_params["threadId"] = resume
        thread_response = session.request(thread_method, thread_params)
        payload = thread_response.get("result")
        thread = payload.get("thread") if isinstance(payload, dict) else None
        thread_id = _id_from(thread, "id") if isinstance(thread, dict) else None
        if thread_id is None:
            raise ValueError("app-server thread response lacked a thread id")
        result["thread_id"] = result["session_id"] = thread_id
        turn_response = session.request("turn/start", {
            "threadId": thread_id, "model": live.model, "effort": live.effort,
            "input": [{"type": "text", "text": task}], "sandboxPolicy": policy})
        payload = turn_response.get("result")
        turn = payload.get("turn") if isinstance(payload, dict) else None
        turn_id = _direct_id(turn, "id")
        if turn_id is None:
            raise ValueError("app-server turn response lacked a turn id")
        result["turn_id"] = turn_id
        terminal = session.read_terminal(thread_id, turn_id)
        result["status"] = _terminal_status(terminal)
    finally:
        session.close()


def run_delegate(args: argparse.Namespace, *, catalog_payload: Optional[Mapping[str, Any]] = None,
                 popen_factory: Callable[..., Any] = subprocess.Popen,
                 environ: Optional[Mapping[str, str]] = None) -> tuple[dict[str, Any], int]:
    """Perform exactly the named transport; test callers may supply a catalog fixture."""
    env = dict(os.environ if environ is None else environ)
    sandbox = "workspace-write" if args.write else "read-only"
    if "PROCTOR_CHILD" in env:
        return _result("refused", transport=args.transport, sandbox=sandbox, error="PROCTOR_CHILD parent refusal"), EXIT_INVALID
    child_env = dict(env); child_env["PROCTOR_CHILD"] = "1"
    result = _result("invalid", transport=args.transport, sandbox=sandbox)
    failure_status = "invalid"
    try:
        deadline = _Deadline(getattr(args, "timeout", 1800))
        workspace = Path(args.workspace).resolve()
        if not workspace.is_dir(): raise ValueError("workspace must be an existing directory")
        task = _outside_workspace(args.task_file, workspace)
        config = _load_config(args.config, args.preset)
        if catalog_payload is None:
            failure_status = "operational_failure"
            catalog_payload = _catalog_rpc(args.codex_executable, popen_factory, child_env, deadline, result)
            failure_status = "invalid"
        selection = resolve_selection(parse_catalog(catalog_payload), config, model=args.model, preset=args.preset, effort=args.effort)
        result.update(model=selection.model, effort=selection.effort)
        if args.transport == "cli" and args.resume_identity is not None:
            raise ValueError("CLI resume is refused: installed resume cannot reapply sandbox/cwd binding")
        resume = _identity(args.resume_identity, transport=args.transport, model=selection.model,
                           effort=selection.effort, sandbox=sandbox)
        failure_status = "operational_failure"
        if args.transport == "cli":
            _cli(args.codex_executable, selection, task, workspace, sandbox, resume, popen_factory,
                 child_env, deadline, result)
        elif args.transport == "app-server":
            # app-server's own model/list response is required and validated before thread work.
            _app_server(args.codex_executable, selection, task, workspace, sandbox, resume, popen_factory,
                        child_env, deadline, result)
        else:
            raise ValueError("transport must be cli or app-server")
    except Exception as exc:
        result.update(status=failure_status, error=type(exc).__name__)
        return result, EXIT_INVALID if failure_status == "invalid" else EXIT_OPERATIONAL
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
    parser.add_argument("--timeout", type=float, default=1800,
                        help="adapter deadline in seconds (default 1800; maximum 7200)")
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
