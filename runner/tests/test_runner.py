#!/usr/bin/env python3
"""Smoke suite for runner/runner.py — MVP-001 acceptance (issue #27).

S1 lane routing, S2 happy path, S3 stale-receipt refusal (#17-class),
S4 config-surface rejection (#18-class), S5 stagnation switch (#26 table),
S6 metering reconciliation with scripts/extract_log.py.

Plus: nested-workspace refusal (#20-class, skipped without git).

Run: python -m unittest discover -s runner/tests -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "runner" / "runner.py"
FAKE_WORKER = ROOT / "runner" / "tests" / "fake_worker.py"
EXTRACT_LOG = ROOT / "scripts" / "extract_log.py"
PRICING = ROOT / "evals" / "pricing.yaml"

BUGGY = 'def sum_to_n(n):\n    return sum(range(1, n))\n'
FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15, f"sum_to_n(5)={sum_to_n(5)}, expected 15"\n'
    'print("PASS")\n'
)


def run_runner(*argv, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=120)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def make_task(tmp, features=None, verifier=None, task_id="t1"):
    task = {
        "task_id": task_id,
        "prompt": "Fix the bug.",
        "features": features or {},
        "scope": ["math_utils.py"],
        "verifier": verifier or {"argv": ["{python}", "check.py"]},
        "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60},
    }
    p = Path(tmp) / "task.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    return str(p)


def make_workspace(tmp, buggy=True):
    ws = Path(tmp) / "ws"
    ws.mkdir()
    (ws / "math_utils.py").write_text(BUGGY if buggy else FIXED, encoding="utf-8")
    (ws / "check.py").write_text(CHECK, encoding="utf-8")
    return str(ws)


class SmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-smoke-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── S1: frozen lane table routes by task features ───────────────────
    def test_s1_lane_routing(self):
        cases = [
            ({"bounded": True, "known_location": True,
              "objective_acceptance": True}, "flash"),
            ({"multi_module": True}, "glm"),
            ({"unfamiliar_repo": True}, "glm"),
            ({"open_ended": True}, "k3"),
            ({}, "glm"),  # default: substantial without bounded signature
        ]
        for features, expected in cases:
            task = make_task(self.tmp, features=features)
            rc, out = run_runner("lane", "--task", task)
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["lane"], expected, features)

    # ── S2: happy path — dispatch fixes the tree, verify green, accept ──
    def test_s2_happy_path(self):
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp, features={
            "bounded": True, "known_location": True, "objective_acceptance": True})
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["lane"], "flash")

        env = {"FAKE_WORKER_WRITE": "math_utils.py", "FAKE_WORKER_CONTENT": FIXED}
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_WORKER), env_extra=env)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["envelope_status"], "completed")
        self.assertEqual(out["agent"], "gpt-oss-worker")

        rc, out = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["passed"], out)

        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["accepted"])

        rc, out = run_runner("record", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        row = out["row"]
        self.assertEqual(row["task_id"], "t1")
        self.assertEqual(row["lane"], "flash")
        self.assertTrue(row["accepted"])
        self.assertEqual(row["dispatches"], 1)
        self.assertTrue(Path(out["log"]).is_file())

    # ── S3: mutation after a green verify stales the receipt (#17) ──────
    def test_s3_stale_receipt_refused(self):
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp)
        run_runner("init", "--workspace", ws, "--task", task)
        env = {"FAKE_WORKER_WRITE": "math_utils.py", "FAKE_WORKER_CONTENT": FIXED}
        run_runner("dispatch", "--workspace", ws, "--task", task,
                   "--delegate", str(FAKE_WORKER), env_extra=env)
        rc, out = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertTrue(out["passed"], out)

        # Tree mutates after the green receipt (re-dispatch, manual edit, ...).
        (Path(ws) / "math_utils.py").write_text(BUGGY, encoding="utf-8")

        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1)
        self.assertFalse(out["accepted"])
        self.assertIn("stale_receipt", out["reason"])

    # ── S4: new verification-affecting file rejects verification (#18) ──
    def test_s4_config_surface_rejection(self):
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp)
        run_runner("init", "--workspace", ws, "--task", task)

        # A worker can neuter pytest without touching scope: root conftest.py.
        (Path(ws) / "conftest.py").write_text(
            "def pytest_collection_modifyitems(config, items):\n"
            "    items.clear()\n", encoding="utf-8")

        rc, out = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1)
        self.assertFalse(out["passed"])
        self.assertEqual(out["rejected"], "config_surface_changed")
        self.assertIn("conftest.py", out["added"])

        # And acceptance is impossible on a rejected receipt.
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1)
        self.assertFalse(out["accepted"])

    # ── S5: identical failure fingerprint x3 -> lateral switch (#26) ────
    def test_s5_stagnation_switch_recommendation(self):
        ws = make_workspace(self.tmp)  # buggy; worker "completes" without fixing
        task = make_task(self.tmp, features={
            "bounded": True, "known_location": True, "objective_acceptance": True})
        run_runner("init", "--workspace", ws, "--task", task)

        last = None
        for attempt in range(3):
            rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                                 "--delegate", str(FAKE_WORKER))
            self.assertEqual(rc, 0, out)
            rc, last = run_runner("verify", "--workspace", ws, "--task", task)
            self.assertEqual(rc, 1)
            self.assertFalse(last["passed"])

        self.assertEqual(last["failure_class"], "execution_stagnation")
        self.assertEqual(last["recommendation"]["action"], "lateral_switch")
        self.assertEqual(last["recommendation"]["to_lane"], "glm")  # flash -> glm

    # ── S6: task record reconciles with extract_log.py extraction ───────
    def test_s6_metering_reconciliation(self):
        ws = make_workspace(self.tmp, buggy=False)
        task = make_task(self.tmp)
        run_runner("init", "--workspace", ws, "--task", task)
        run_runner("verify", "--workspace", ws, "--task", task)

        wire = Path(self.tmp) / "wire.jsonl"
        events = [
            {"type": "usage.record", "model": "fireworks/kimi-k3", "usage": {
                "inputOther": 1000, "output": 200,
                "inputCacheRead": 5000, "inputCacheCreation": 500}},
            {"type": "usage.record", "model": "fireworks/kimi-k3", "usage": {
                "inputOther": 2000, "output": 300,
                "inputCacheRead": 7000, "inputCacheCreation": 0}},
            {"type": "step.end", "model": "fireworks/kimi-k3", "usage": {
                "inputOther": 99999, "output": 99999,
                "inputCacheRead": 0, "inputCacheCreation": 0}},
        ]
        wire.write_text("".join(json.dumps(e) + "\n" for e in events),
                        encoding="utf-8")

        rc, out = run_runner("record", "--workspace", ws, "--task", task,
                             "--wire", str(wire), "--pricing", str(PRICING))
        self.assertEqual(rc, 0, out)
        row = out["row"]

        # usage.record only — step.end would double-count (meter.py idiom).
        self.assertEqual(row["usage_records"], 2)
        toks = row["tokens_by_model"]["fireworks/kimi-k3"]
        self.assertEqual(toks["inputOther"], 3000)
        self.assertEqual(toks["output"], 500)
        self.assertEqual(toks["inputCacheRead"], 12000)
        self.assertEqual(toks["inputCacheCreation"], 500)

        # K3 prices: 3.00 input / 0.30 cached / 15.00 output per 1M.
        expected = round((3000 + 500) * 3.00 / 1e6
                         + 12000 * 0.30 / 1e6
                         + 500 * 15.00 / 1e6, 6)
        self.assertEqual(row["api_cost_usd"], expected)

        # Reconcile against the extractor's independent read of the same wire.
        spec = importlib_import(EXTRACT_LOG)
        facts, coverage = spec.extract_file(wire)
        self.assertEqual(facts["usage_records"], row["usage_records"])
        self.assertEqual(coverage["records_unrecognized"], 0)
        self.assertEqual(coverage["malformed_lines"], 0)


def importlib_import(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(shutil.which("git"), "git not available")
class GitWorkspaceTest(unittest.TestCase):
    """#20: a workspace nested in a parent repo must be refused."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-git-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_nested_workspace_refused(self):
        parent = Path(self.tmp) / "parent"
        nested = parent / "subproject"
        nested.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=parent, capture_output=True, check=True)
        task = make_task(self.tmp)
        rc, out = run_runner("init", "--workspace", str(nested), "--task", task)
        self.assertEqual(rc, 1)
        self.assertEqual(out["error"], "workspace_is_not_repo_root")

    def test_repo_root_accepted(self):
        ws = Path(self.tmp) / "repo"
        ws.mkdir()
        (ws / "math_utils.py").write_text(FIXED, encoding="utf-8")
        (ws / "check.py").write_text(CHECK, encoding="utf-8")
        subprocess.run(["git", "init"], cwd=ws, capture_output=True, check=True)
        task = make_task(self.tmp)
        rc, out = run_runner("init", "--workspace", str(ws), "--task", task)
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
