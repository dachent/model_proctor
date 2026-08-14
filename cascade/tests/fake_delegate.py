#!/usr/bin/env python3
"""Fixture fake for the delegate wrapper — emits canned JSON envelopes.

Used ONLY by cascade/tests/test_cascade.py via the CASCADE_DELEGATE override;
no real CLI is ever launched. Speaks the FUTURE delegate interface
(envelope field ``child_session_id``, ``--resume-from <session_id>`` flag)
that the installed delegate/delegate.py does not implement yet.

Behavior is selected with the FAKE_DELEGATE_MODE env var:
  completed       envelope status completed, exit 0 (default)
  failed          envelope status failed, child_exit_code 1, exit 0
  timeout         envelope status timeout, exit 124
  internal_error  envelope status internal_error, exit 70
  interrupted     envelope status interrupted, exit 130
  garbage         prints non-JSON noise, exit 0 (envelope-parse failure)

Extra knobs (orthogonal to mode):
  FAKE_DELEGATE_RUN_DIR  if set, the envelope's run_dir points here (tests
                         pre-populate it with stdout.log/stderr.log so the
                         controller's evidence archiving can be exercised)
  FAKE_DELEGATE_WRITE    workspace-relative file path the "worker" writes
                         (content: "written by fake delegate") before emitting
                         the envelope — simulates worker tree mutations

Every invocation appends one JSON line (argv + task text) to the file named
by FAKE_DELEGATE_CALLS, so tests can assert --resume-from plumbing.
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
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--task")
    group.add_argument("--task-file")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--resume-from", default=None)
    args = parser.parse_args()

    task_text = ""
    if args.task_file:
        task_text = Path(args.task_file).read_text(encoding="utf-8")
    elif args.task:
        task_text = args.task

    calls_file = os.environ.get("FAKE_DELEGATE_CALLS")
    if calls_file:
        with open(calls_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "agent": args.agent,
                "workspace": args.workspace,
                "timeout": args.timeout,
                "resume_from": args.resume_from,
                "task": task_text,
            }) + "\n")

    mode = os.environ.get("FAKE_DELEGATE_MODE", "completed")

    # Simulate a worker tree mutation (tracked edit or new file).
    write_rel = os.environ.get("FAKE_DELEGATE_WRITE")
    if write_rel:
        target = Path(args.workspace) / write_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("written by fake delegate\n", encoding="utf-8")

    if mode == "garbage":
        sys.stdout.write("this is not a JSON envelope at all\n")
        sys.stdout.write("{broken json\n")
        return 0

    status, child_rc, exit_code = {
        "completed": ("completed", 0, 0),
        "failed": ("failed", 1, 0),
        "timeout": ("timeout", None, 124),
        "internal_error": ("internal_error", None, 70),
        "interrupted": ("interrupted", None, 130),
    }[mode]

    envelope = {
        "schema_version": 1,
        "status": status,
        "agent": args.agent,
        "child_exit_code": child_rc,
        "duration_seconds": 0.01,
        "stdout": f"fake stdout from {args.agent} (mode={mode})",
        "stderr": "",
        "stdout_truncated": False,
        "stderr_truncated": False,
        "stdout_log_truncated": False,
        "stderr_log_truncated": False,
        "run_dir": os.environ.get("FAKE_DELEGATE_RUN_DIR"),
        "acl_warning": False,
        "job_warning": False,
        "error": None if status in ("completed", "failed") else f"fake_{status}",
        "child_session_id": "fake-session-0001" if status in ("completed", "failed") else None,
    }
    sys.stdout.write(json.dumps(envelope) + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
