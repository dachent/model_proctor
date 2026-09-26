#!/usr/bin/env python3
"""--print-timeout-knobs contract tests (#108 TOOL-037).

The runner owns the stack report, but the delegate owns its config — so the
delegate reports its own timeout knobs. A config that fails validation is
still reported (knobs_valid: false) so the caller can NAME the violation
instead of crashing on it (Review Focus #3).
"""
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
EXAMPLE_CONFIG = DELEGATE_DIR / "agents.example.json"


def run_knobs(env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(DELEGATE), "--print-timeout-knobs"],
                       capture_output=True, text=True, env=env, timeout=30)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


class TimeoutKnobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="knobs-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_example_config_reports_knobs(self):
        rc, out = run_knobs({"DELEGATE_CONFIG": str(EXAMPLE_CONFIG)})
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["knobs_valid"])
        self.assertEqual(out["default_kill_grace_seconds"], 5)
        self.assertEqual(out["max_timeout_seconds"], 7200)
        self.assertEqual(out["agents"]["example-coder"]["default_timeout"], 1800)

    def test_invalid_config_is_reported_not_crash(self):
        # Grace above the delegate's own cap (60) would invert the stack
        # against the runner breaker; the report must surface it by name.
        # (_validate_config checks grace at :417-421, before the agents
        # loop, so this minimal agent is never validated.)
        bad = Path(self.tmp) / "bad.json"
        bad.write_text(json.dumps({
            "allowed_workspace_roots": [self.tmp],
            "max_task_bytes": 1024, "max_timeout_seconds": 7200,
            "default_kill_grace_seconds": 5000,
            "max_stdout_bytes": 1024, "max_stderr_bytes": 1024,
            "agents": {"a": {"command": ["x"], "prompt_delivery": "stdin",
                             "default_timeout": 60, "minimum_timeout": 1,
                             "maximum_timeout": 120}}}), encoding="utf-8")
        rc, out = run_knobs({"DELEGATE_CONFIG": str(bad)})
        self.assertEqual(rc, 64, out)
        self.assertFalse(out["knobs_valid"])
        self.assertIn("default_kill_grace_seconds", out["error"])
        self.assertEqual(out["default_kill_grace_seconds"], 5000)

    def test_missing_config_is_reported_not_crash(self):
        rc, out = run_knobs(
            {"DELEGATE_CONFIG": str(Path(self.tmp) / "nope.json")})
        self.assertEqual(rc, 64, out)
        self.assertFalse(out["knobs_valid"])
        self.assertIn("error", out)


class StallGuardKnobs(unittest.TestCase):
    """TOOL-035/037: the probe must surface the stall_guard ladder — the
    runner's timeouts report composes it into the true worst case."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="knobs-sg-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _config(self, stall_block):
        base = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        if stall_block is None:
            base.pop("stall_guard", None)
        else:
            base["stall_guard"] = stall_block
        p = Path(self.tmp) / "agents.json"
        p.write_text(json.dumps(base), encoding="utf-8")
        return p

    def test_absent_block_reports_not_configured(self):
        rc, out = run_knobs({"DELEGATE_CONFIG": str(self._config(None))})
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["stall_guard"]["configured"], False)
        self.assertEqual(out["stall_guard"]["enabled"], False)
        self.assertEqual(out["stall_guard"]["worst_case_added_s"], 0.0)

    def test_disabled_block_reports_zero_ladder(self):
        rc, out = run_knobs({"DELEGATE_CONFIG": str(EXAMPLE_CONFIG)})
        self.assertEqual(rc, 0, out)
        sg = out["stall_guard"]
        self.assertTrue(sg["configured"])
        self.assertFalse(sg["enabled"])
        self.assertEqual(sg["worst_case_added_s"], 0.0)
        self.assertEqual(sg["max_added_seconds_cap"], 60.0)

    def test_enabled_block_reports_worst_case(self):
        cfg = self._config({"enabled": True})
        rc, out = run_knobs({"DELEGATE_CONFIG": str(cfg)})
        self.assertEqual(rc, 0, out)
        sg = out["stall_guard"]
        self.assertTrue(sg["enabled"])
        self.assertEqual(sg["worst_case_added_s"], 60.0)
        self.assertEqual(sg["max_added_seconds_cap"], 60.0)


if __name__ == "__main__":
    unittest.main()
