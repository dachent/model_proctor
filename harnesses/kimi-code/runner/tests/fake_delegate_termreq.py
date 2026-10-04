#!/usr/bin/env python3
"""Fixture fake for the delegate wrapper — TOOL-036 termination-request tests.

Speaks the delegate CLI contract plus --terminate-request-file. Modes
(env FAKE_DELEGATE_MODE):
  honor   poll for the request file; on appearance emit an interrupted
          envelope attributed delegate:runner_requested, exit 130.
  ignore  sleep 30s ignoring the request file (a wedged delegate stand-in).

Env knobs:
  FAKE_DELEGATE_PID_FILE  write own pid here at startup
  FAKE_DELEGATE_SIDECAR   write {"request_file_existed_at_start": bool} here
"""

import argparse
import json
import os
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--terminate-request-file", default=None)
    args, _unknown = parser.parse_known_args()

    pid_file = os.environ.get("FAKE_DELEGATE_PID_FILE")
    if pid_file:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
    existed_at_start = bool(args.terminate_request_file
                            and os.path.exists(args.terminate_request_file))
    sidecar = os.environ.get("FAKE_DELEGATE_SIDECAR")
    if sidecar:
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump({"request_file_existed_at_start": existed_at_start}, f)

    if os.environ.get("FAKE_DELEGATE_MODE", "honor") == "honor":
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if (args.terminate_request_file
                    and os.path.exists(args.terminate_request_file)):
                sys.stdout.write(json.dumps({
                    "schema_version": 1, "status": "interrupted",
                    "agent": args.agent, "child_exit_code": None,
                    "child_session_id": None, "child_home": None,
                    "duration_seconds": 0.1, "stdout": "", "stderr": "",
                    "stdout_truncated": False, "stderr_truncated": False,
                    "stdout_log_truncated": False, "stderr_log_truncated": False,
                    "run_dir": None, "acl_warning": False, "job_warning": False,
                    "kill_authority": "delegate:runner_requested",
                    "error": "termination requested via --terminate-request-file",
                }) + "\n")
                sys.stdout.flush()
                return 130
            time.sleep(0.1)
    else:
        time.sleep(30)
    sys.stdout.write(json.dumps({
        "schema_version": 1, "status": "completed", "agent": args.agent,
        "child_exit_code": 0, "child_session_id": None, "child_home": None,
        "duration_seconds": 0.1, "stdout": "", "stderr": "",
        "stdout_truncated": False, "stderr_truncated": False,
        "stdout_log_truncated": False, "stderr_log_truncated": False,
        "run_dir": None, "acl_warning": False, "job_warning": False,
        "kill_authority": "none", "error": None,
    }) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
