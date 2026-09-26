#!/usr/bin/env python3
"""Timeout-stack coherence at the runner boundary (#108 TOOL-037).

Pins: every runner ceiling is the DERIVED breaker (a ceiling derived
anywhere else can mark a legally-alive dispatch orphaned — Review Focus
#2); `runner.py timeouts` is the discoverability surface and the preflight
that refuses inverted stacks; the flat install resolves the authority or
refuses loudly (Review Focus #5).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # harnesses/kimi-code
RUNNER = ROOT / "runner" / "runner.py"
DELEGATE = ROOT / "delegate" / "delegate.py"
REPO_ROOT = ROOT.parents[1]
EXAMPLE_CONFIG = ROOT / "delegate" / "agents.example.json"

sys.path.insert(0, str(REPO_ROOT / "core"))
import timeout_stack as ts  # noqa: E402


def run_runner(*argv, env_extra=None, timeout=120):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=timeout)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def make_task(tmp, **overrides):
    task = {"task_id": "t1", "prompt": "Fix the bug.",
            "features": {"bounded": True},
            "scope": ["math_utils.py"],
            "verifier": {"argv": ["{python}", "check.py"]},
            "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60}}
    task.update(overrides)
    p = Path(tmp) / "task.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    return str(p)


def make_workspace(tmp):
    ws = Path(tmp) / "ws"
    ws.mkdir()
    (ws / "math_utils.py").write_text(
        "def sum_to_n(n):\n    return sum(range(1, n + 1))\n", encoding="utf-8")
    (ws / "check.py").write_text("print('PASS')\n", encoding="utf-8")
    return str(ws)


def state_root_for(ws):
    import hashlib
    import re
    ws_res = Path(ws).resolve()
    key = re.sub(r"[^A-Za-z0-9_.-]+", "_", ws_res.name)
    digest = hashlib.sha256(str(ws_res).encode("utf-8")).hexdigest()[:8]
    return ws_res.parent / ".runner-state" / f"{key}-{digest}"


class TimeoutsCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tc-")
        self.task = make_task(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_happy_path_reports_the_whole_stack(self):
        rc, out = run_runner(
            "timeouts", "--task", self.task, "--delegate", str(DELEGATE),
            env_extra={"DELEGATE_CONFIG": str(EXAMPLE_CONFIG)})
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["coherent"], out)
        self.assertEqual(out["violations"], [])
        seconds = [l["seconds"] for l in out["layers"]]
        self.assertEqual(seconds, sorted(seconds))
        self.assertEqual([l["layer"] for l in out["layers"]],
                         ["delegate_self_abort", "delegate_ceiling",
                          "runner_breaker", "pilot_breaker"])
        self.assertEqual(out["delegate_knobs"]["default_kill_grace_seconds"], 5)
        self.assertEqual(out["budget"]["verify_timeout_s"],
                         ts.DEFAULT_VERIFY_TIMEOUT_S)

    def test_inverted_delegate_config_is_refused_by_name(self):
        bad = Path(self.tmp) / "bad-agents.json"
        bad.write_text(json.dumps({
            "allowed_workspace_roots": [self.tmp],
            "max_task_bytes": 1024, "max_timeout_seconds": 7200,
            "default_kill_grace_seconds": 5000,
            "max_stdout_bytes": 1024, "max_stderr_bytes": 1024,
            "agents": {"a": {"command": ["x"], "prompt_delivery": "stdin",
                             "default_timeout": 60, "minimum_timeout": 1,
                             "maximum_timeout": 120}}}), encoding="utf-8")
        rc, out = run_runner(
            "timeouts", "--task", self.task, "--delegate", str(DELEGATE),
            env_extra={"DELEGATE_CONFIG": str(bad)})
        self.assertEqual(rc, 1, out)
        self.assertFalse(out["coherent"])
        self.assertTrue(any("delegate_config_invalid" in v
                            for v in out["violations"]), out)


class BreakerCeilingParity(unittest.TestCase):
    """Journal hand-writing idiom from test_dispatch_journal.py:123-150."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tcp-")
        self.ws = make_workspace(self.tmp)
        self.task = make_task(self.tmp)
        rc, out = run_runner("init", "--workspace", self.ws, "--task", self.task)
        self.assertEqual(rc, 0, out)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_open(self, dispatch_id, age_s):
        journal = state_root_for(self.ws) / "journal.jsonl"
        at = time.strftime("%Y-%m-%dT%H:%M:%S",
                           time.localtime(time.time() - age_s))
        with open(journal, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "dispatch_open", "task_id": "t1",
                "dispatch_id": dispatch_id, "agent": "a",
                "timeout_s": 60, "runner_pid": 0, "at": at}) + "\n")

    def test_status_orphan_ceiling_is_the_derived_breaker(self):
        self._write_open("d-outside", ts.runner_breaker_s(60) + 5)
        self._write_open("d-inside", ts.runner_breaker_s(60) - 5)
        rc, out = run_runner("status", "--workspace", self.ws)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["orphaned_dispatch_ids"], ["d-outside"])
        self.assertIn("d-inside", out["open_journal_ids"])

    def test_named_timeout_envelope_not_breaker_preemption(self):
        # Acceptance #2: a wedged inner job yields the inner layer's named
        # error. The fake delegate emits its own timeout envelope promptly;
        # the runner must pass it through, never its synthetic
        # delegate_wrapper_timeout.
        env = {"FAKE_WORKER_MODE": "timeout"}
        rc, out = run_runner("dispatch", "--workspace", self.ws,
                             "--task", self.task,
                             "--delegate",
                             str(ROOT / "runner" / "tests" / "fake_worker.py"),
                             env_extra=env)
        self.assertEqual(out["envelope_status"], "timeout", out)
        self.assertNotEqual(out.get("envelope_status"), "delegate_wrapper_timeout")


class FlatInstallLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tcf-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _flat(self, with_stack=True):
        flat = Path(self.tmp) / "flat"
        flat.mkdir(exist_ok=True)
        shutil.copy2(RUNNER, flat / "runner.py")
        shutil.copy2(REPO_ROOT / "core" / "task_schema.py",
                     flat / "task_schema.py")
        if with_stack:
            shutil.copy2(REPO_ROOT / "core" / "timeout_stack.py",
                         flat / "timeout_stack.py")
        return flat

    def test_flat_install_layout_resolves_timeout_stack(self):
        flat = self._flat(with_stack=True)
        task = make_task(self.tmp)
        r = subprocess.run([sys.executable, str(flat / "runner.py"),
                            "lane", "--task", task],
                           capture_output=True, text=True, timeout=120)
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("Traceback", r.stderr)

    def test_flat_install_missing_module_refuses_loudly(self):
        flat = self._flat(with_stack=False)
        task = make_task(self.tmp)
        r = subprocess.run([sys.executable, str(flat / "runner.py"),
                            "lane", "--task", task],
                           capture_output=True, text=True, timeout=120)
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        self.assertEqual(r.returncode, 4, out)
        self.assertEqual(out["error"], "timeout_stack_module_missing")


if __name__ == "__main__":
    unittest.main()
