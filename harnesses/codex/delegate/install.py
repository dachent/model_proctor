#!/usr/bin/env python3
"""Copy the flat Codex delegate to an explicitly chosen destination.

There is intentionally no default destination: the Kimi installer owns its
own durable location and this adapter must never create or overwrite one by
surprise.  Local config, credentials, and executable paths are not inputs to
this installer.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Sequence


SOURCE_DIR = Path(__file__).resolve().parent
INSTALL_MANIFEST = (
    "delegate.py",
    "catalog.py",
    "local-config.example.json",
    "README.md",
)


def install(destination: Path) -> list[Path]:
    """Copy only the flat adapter's tracked runtime and operator artifacts."""
    if destination.exists() and not destination.is_dir():
        raise ValueError("--destination must name a directory")
    sources = [SOURCE_DIR / name for name in INSTALL_MANIFEST]
    missing = [source.name for source in sources if not source.is_file()]
    if missing:
        raise ValueError(f"installer source manifest is incomplete: {missing}")
    collisions = [name for name in INSTALL_MANIFEST
                  if (destination / name).exists() or (destination / name).is_symlink()]
    if collisions:
        raise ValueError(f"destination already contains manifest names: {collisions}")
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for source in sources:
        target = destination / source.name
        shutil.copy2(source, target)
        copied.append(target)
    return copied


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-delegate-install")
    parser.add_argument(
        "--destination", required=True, type=Path,
        help="explicit directory to receive the flat Codex adapter",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    copied = install(args.destination)
    print("copied " + ", ".join(path.name for path in copied) + f" to {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
