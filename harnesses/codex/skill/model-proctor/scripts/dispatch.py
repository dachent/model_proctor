#!/usr/bin/env python3
"""Invoke the installed bounded adapter with one Codex skill task."""
from __future__ import annotations

import argparse
import base64
import binascii
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence, TextIO


DEFAULT_ADAPTER_DIR = Path(r"C:\Tools\model-proctor\codex-delegate")
PRESETS = ("luna", "terra", "astra")


def _desktop_codex_executable(local_app_data: Optional[str] = None) -> Path:
    """Resolve a real desktop-bundled executable instead of PATH's unrelated CLI."""
    root = Path(local_app_data or os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    candidates = [path.resolve() for path in root.glob("*/codex.exe") if path.is_file()]
    if not candidates:
        raise ValueError("desktop-bundled Codex executable was not found")
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, str(path).lower()))


def _read_utf8_task(stdin: TextIO) -> str:
    """Decode a native pipe explicitly, independent of the Windows console code page."""
    binary = getattr(stdin, "buffer", None)
    if binary is None:
        return stdin.read()
    raw = binary.read()
    if not isinstance(raw, bytes):
        raise ValueError("task standard input must provide bytes")
    return raw.decode("utf-8")


def _decode_base64_task(value: str) -> str:
    """Accept task data that a shell cannot safely express as a literal."""
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("base64 task data must be valid UTF-8 base64 data") from exc


def _read_base64_stdin_task(stdin: TextIO) -> str:
    """Read ASCII Base64 from a native pipe without routing task data through argv."""
    binary = getattr(stdin, "buffer", None)
    if binary is None:
        encoded = stdin.read()
    else:
        raw = binary.read()
        if not isinstance(raw, bytes):
            raise ValueError("task standard input must provide bytes")
        try:
            encoded = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("--task-base64-stdin must contain ASCII base64 data") from exc
    return _decode_base64_task(encoded.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="model-proctor-dispatch")
    parser.add_argument("--preset", choices=PRESETS, required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--transport", choices=("cli", "app-server"), default="app-server")
    parser.add_argument("--adapter-dir", default=str(DEFAULT_ADAPTER_DIR))
    parser.add_argument("--config")
    parser.add_argument("--codex-executable")
    parser.add_argument("--task-base64-stdin", action="store_true")
    return parser


def dispatch(args: argparse.Namespace, *, stdin: TextIO = sys.stdin) -> int:
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        raise ValueError("workspace must be an existing directory")
    adapter_dir = Path(args.adapter_dir).resolve()
    adapter = adapter_dir / "delegate.py"
    config = Path(args.config).resolve() if args.config else adapter_dir / "local-config.json"
    if not adapter.is_file():
        raise ValueError("installed model-proctor adapter delegate.py was not found")
    if not config.is_file():
        raise ValueError("installed model-proctor local-config.json was not found")
    codex = Path(args.codex_executable).resolve() if args.codex_executable else _desktop_codex_executable()
    if not codex.is_file():
        raise ValueError("selected Codex executable was not found")
    task = _read_base64_stdin_task(stdin) if args.task_base64_stdin else _read_utf8_task(stdin)
    if not task.strip():
        raise ValueError("task standard input must not be empty")
    command = [sys.executable, str(adapter), "--preset", args.preset, "--config", str(config),
               "--transport", args.transport, "--codex-executable", str(codex),
               "--workspace", str(workspace), "--task-stdin"]
    if args.write:
        command.append("--write")
    return subprocess.run(command, input=task.encode("utf-8"), check=False).returncode


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        return dispatch(build_parser().parse_args(argv))
    except (OSError, ValueError) as exc:
        print(f"model-proctor dispatcher: {exc}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main())
