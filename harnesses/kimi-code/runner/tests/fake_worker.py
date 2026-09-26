#!/usr/bin/env python3
"""Fixture fake for the delegate wrapper — runner smoke tests only.

Speaks the delegate CLI contract (--agent --workspace --task-file --timeout)
and emits one JSON envelope on stdout. No real CLI is ever launched.

Env knobs:
  FAKE_WORKER_MODE      completed (default) | failed | timeout
  FAKE_WORKER_WRITE     workspace-relative file the "worker" writes
  FAKE_WORKER_CONTENT   content to write (default: "written by fake worker")
  FAKE_WORKER_HEARTBEAT 1 -> append schema-v1 heartbeat lines ("intake" before
                        the sleep, "implement" after) to --heartbeat-file (#105)
  FAKE_WORKER_SLEEP     seconds to sleep between the two beats (or before the
                        envelope when not emitting)
  FAKE_WORKER_KILL_EVIDENCE  JSON string; attached to the envelope as
                        kill_evidence (TOOL-035 propagation tests)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--dispatch-id", default=None)
    parser.add_argument("--heartbeat-file", default=None)
    # TOOL-036: the runner forwards --terminate-request-file verbatim too;
    # the fake never wedges, so accept and ignore.
    parser.add_argument("--terminate-request-file", default=None)
    args = parser.parse_args()

    write_rel = os.environ.get("FAKE_WORKER_WRITE")
    if write_rel:
        target = Path(args.workspace) / write_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(os.environ.get("FAKE_WORKER_CONTENT",
                                         "written by fake worker\n"),
                          encoding="utf-8")

    # #105: the runner forwards --heartbeat-file/--dispatch-id verbatim to
    # whatever --delegate names. The fake plays delegate AND payload in one:
    # with FAKE_WORKER_HEARTBEAT=1 it appends schema-v1 heartbeat lines —
    # "intake" BEFORE the sleep and "implement" AFTER, so a sleep spanning
    # one 10s runner tick lets the mid-run drain and the final drain each
    # observe a distinct record (read_latest returns only the newest).
    sleep_s = float(os.environ.get("FAKE_WORKER_SLEEP", "0") or 0)
    if args.heartbeat_file and os.environ.get("FAKE_WORKER_HEARTBEAT"):
        hb = Path(args.heartbeat_file)
        hb.parent.mkdir(parents=True, exist_ok=True)

        def _beat(stage, seq):
            now = time.time()
            with open(hb, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "v": 1,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S",
                                        time.localtime(now)),
                    "epoch": now, "pid": os.getpid(), "seq": seq,
                    "dispatch_id": args.dispatch_id,
                    "stage": stage, "sub_stage": None, "counters": {},
                }) + "\n")

        _beat("intake", 1)
        if sleep_s > 0:
            time.sleep(sleep_s)
        _beat("implement", 2)
    elif sleep_s > 0:
        time.sleep(sleep_s)

    mode = os.environ.get("FAKE_WORKER_MODE", "completed")
    status, child_rc, exit_code = {
        "completed": ("completed", 0, 0),
        "failed": ("failed", 1, 0),
        "timeout": ("timeout", None, 124),
    }[mode]

    envelope = {
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
    }
    evidence_json = os.environ.get("FAKE_WORKER_KILL_EVIDENCE")
    if evidence_json:
        envelope["kill_evidence"] = json.loads(evidence_json)
    sys.stdout.write(json.dumps(envelope) + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
