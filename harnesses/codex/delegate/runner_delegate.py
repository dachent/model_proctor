#!/usr/bin/env python3
"""Adapt the unchanged Kimi runner delegate contract to the Codex transport."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional, Sequence


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import delegate  # noqa: E402


_BINDINGS = {
    "luna": ("luna", True),
    "terra": ("terra", False),
    "astra": ("astra", False),
}


class _EnvelopeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise argparse.ArgumentError(None, message)


def _argv_agent(argv: Sequence[str]) -> Optional[str]:
    for index, value in enumerate(argv):
        if value.startswith("--agent="):
            return value.split("=", 1)[1]
        if value == "--agent":
            if index + 1 < len(argv):
                return argv[index + 1]
    return None


def _local_config() -> Path:
    path = _HERE / "local-config.json"
    if not path.is_file():
        raise FileNotFoundError("sibling local-config.json was not found")
    return path.resolve()


def _desktop_codex_executable() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    candidates = [path.resolve() for path in root.glob("*/codex.exe") if path.is_file()]
    if not candidates:
        raise FileNotFoundError("desktop-bundled Codex executable was not found")
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, str(path).lower()))


def _status(codex_status: Any, codex_error: Any) -> str:
    if codex_status in ("completed", "failed"):
        return codex_status
    if codex_status == "operational_failure" and codex_error == "TransportTimeout":
        return "timeout"
    if codex_status in ("timeout",):
        return "timeout"
    if codex_status in ("refused", "invalid"):
        return "invalid"
    return "internal_error"


def _envelope(*, agent: str, started: float, codex: dict[str, Any], codex_exit_code: int) -> dict[str, Any]:
    codex_status = codex.get("status")
    codex_error = codex.get("error")
    result = dict(codex)
    result.update({
        "status": _status(codex_status, codex_error),
        "agent": agent,
        "duration_seconds": round(time.monotonic() - started, 3),
        "child_session_id": codex.get("session_id") or codex.get("thread_id"),
        "child_home": None,
        "codex_status": codex_status,
        "codex_error": codex_error,
        "codex_exit_code": codex_exit_code,
    })
    return result


def _invalid_envelope(*, agent: str, started: float, error: Exception, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "agent": agent,
        "duration_seconds": round(time.monotonic() - started, 3),
        "child_session_id": None,
        "child_home": None,
        "codex_status": None,
        "codex_error": type(error).__name__,
        "codex_exit_code": None,
        "terminal_evidence": [],
        "error": type(error).__name__,
    }


def run_runner_delegate(args: argparse.Namespace) -> dict[str, Any]:
    """Run one fixed Codex binding and normalize it for the runner."""
    started = time.monotonic()
    agent = args.agent
    try:
        preset, write = _BINDINGS[agent]
    except KeyError as exc:
        return _invalid_envelope(agent=agent, started=started, error=exc, status="invalid")
    try:
        child = argparse.Namespace(
            model=None,
            preset=preset,
            transport="app-server",
            task_file=args.task_file,
            task_stdin=False,
            workspace=args.workspace,
            write=write,
            effort=None,
            codex_executable=str(_desktop_codex_executable()),
            config=str(_local_config()),
            resume_identity=None,
            timeout=args.timeout,
        )
        codex, code = delegate.run_delegate(child)
        return _envelope(agent=agent, started=started, codex=codex, codex_exit_code=code)
    except Exception as exc:
        return _invalid_envelope(agent=agent, started=started, error=exc, status="internal_error")


def build_parser() -> argparse.ArgumentParser:
    parser = _EnvelopeParser(prog="codex-runner-delegate")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--timeout", type=float, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    started = time.monotonic()
    try:
        args = build_parser().parse_args(raw_argv)
    except argparse.ArgumentError as exc:
        print(json.dumps(_invalid_envelope(agent=_argv_agent(raw_argv), started=started,
                                            error=exc, status="invalid"),
                         separators=(",", ":")))
        return 0
    print(json.dumps(run_runner_delegate(args), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
