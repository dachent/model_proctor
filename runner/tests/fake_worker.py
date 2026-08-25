#!/usr/bin/env python3
"""Fixture fake for the delegate wrapper — runner smoke tests only.

Speaks the delegate CLI contract (--agent --workspace --task-file --timeout)
and emits one JSON envelope on stdout. No real CLI is ever launched.

Env knobs:
  FAKE_WORKER_MODE      completed (default) | failed | timeout
  FAKE_WORKER_WRITE     workspace-relative file the "worker" writes
  FAKE_WORKER_CONTENT   content to write (default: "written by fake worker")
"""

import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--timeout", type=float, default=None)
    args = parser.parse_args()

    write_rel = os.environ.get("FAKE_WORKER_WRITE")
    if write_rel:
        target = Path(args.workspace) / write_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(os.environ.get("FAKE_WORKER_CONTENT",
                                         "written by fake worker\n"),
                          encoding="utf-8")

    mode = os.environ.get("FAKE_WORKER_MODE", "completed")
    status, child_rc, exit_code = {
        "completed": ("completed", 0, 0),
        "failed": ("failed", 1, 0),
        "timeout": ("timeout", None, 124),
    }[mode]

    sys.stdout.write(json.dumps({
        "schema_version": 1,
        "status": status,
        "agent": args.agent,
        "child_exit_code": child_rc,
        "duration_seconds": 0.01,
        "stdout": f"fake worker {args.agent} (mode={mode})",
        "stderr": "",
        "run_dir": None,
        "error": None if status in ("completed", "failed") else f"fake_{status}",
        "child_session_id": "fake-session-0001" if status == "completed" else None,
    }) + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
