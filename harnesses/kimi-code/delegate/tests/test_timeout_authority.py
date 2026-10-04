#!/usr/bin/env python3
"""Cross-pins to the one timeout sizing authority (#108 TOOL-037).

delegate.py's grace cap and stall_guard.MAX_ADDED_SECONDS are load-bearing
literals in the composed stack (core/timeout_stack.py's module docstring).
These tests fail when either drifts from the authority constants or from
the runner's TOOL-036 termination-request window.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DELEGATE_DIR = Path(__file__).resolve().parents[1]
DELEGATE = DELEGATE_DIR / "delegate.py"
REPO_ROOT = DELEGATE_DIR.parents[2]
RUNNER = REPO_ROOT / "harnesses" / "kimi-code" / "runner" / "runner.py"

sys.path.insert(0, str(DELEGATE_DIR))
sys.path.insert(0, str(REPO_ROOT / "core"))

import stall_guard  # noqa: E402
import timeout_stack as ts  # noqa: E402


def _runner_request_wait_s():
    # importlib loads a fresh module OBJECT (runner.py is import-safe;
    # main() is name-guarded), so this reads the runner's own constant
    # rather than re-stating it here.
    spec = importlib.util.spec_from_file_location("runner", str(RUNNER))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner.TERMINATION_REQUEST_WAIT_S


def _config(tmp, grace):
    p = Path(tmp) / "agents.json"
    p.write_text(json.dumps({
        "allowed_workspace_roots": [tmp],
        "max_task_bytes": 1024, "max_timeout_seconds": 7200,
        "default_kill_grace_seconds": grace,
        "max_stdout_bytes": 1024, "max_stderr_bytes": 1024,
        "agents": {"a": {"command": [sys.executable],
                         "prompt_delivery": "stdin",
                         "default_timeout": 60, "minimum_timeout": 1,
                         "maximum_timeout": 120}}}), encoding="utf-8")
    return p


def run_knobs(config):
    env = dict(os.environ, DELEGATE_CONFIG=str(config))
    r = subprocess.run([sys.executable, str(DELEGATE), "--print-timeout-knobs"],
                       capture_output=True, text=True, env=env, timeout=30)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


class GraceCapPin(unittest.TestCase):
    """delegate.py's grace cap literal IS timeout_stack.KILL_GRACE_MAX_S."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tap-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_grace_at_the_authority_max_is_legal(self):
        rc, out = run_knobs(_config(self.tmp, ts.KILL_GRACE_MAX_S))
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["knobs_valid"], out)

    def test_grace_past_the_authority_max_is_refused(self):
        rc, out = run_knobs(_config(self.tmp, ts.KILL_GRACE_MAX_S + 1))
        self.assertEqual(rc, 64, out)
        self.assertFalse(out["knobs_valid"])
        self.assertIn("default_kill_grace_seconds", out["error"])


class StallGuardCapPin(unittest.TestCase):
    """MAX_ADDED_SECONDS stays inside the composed stack: the delegate's
    absolute worst case (ladder + worst legal grace + overhead) plus the
    report margin must fit the runner breaker PLUS the TOOL-036
    termination-request window (timeout_stack.py docstring, Ruling
    2026-09-26)."""

    def test_ladder_cap_within_breaker_plus_request_window(self):
        request_wait = _runner_request_wait_s()
        worst = (stall_guard.MAX_ADDED_SECONDS + ts.KILL_GRACE_MAX_S
                 + ts.DELEGATE_OVERHEAD_S)
        ceiling = ts.RUNNER_BREAKER_MARGIN_S + request_wait
        self.assertLessEqual(worst + ts.MIN_REPORT_MARGIN_S, ceiling)

    def test_default_policy_worst_case_within_cap(self):
        p = stall_guard.StallPolicy.from_config({"enabled": True})
        self.assertLessEqual(p.worst_case_added_s(),
                             stall_guard.MAX_ADDED_SECONDS)


if __name__ == "__main__":
    unittest.main()
