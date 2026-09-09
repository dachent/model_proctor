#!/usr/bin/env python3
"""Runner gate tests for the shared task schema (#83 TOOL-030 M0 / A12).

The schema is policy in core/task_schema.py; these tests pin that the kimi
runner actually refuses malformed task files THROUGH it at the command
boundary (load_task), with the runner's own error envelope and exit code 3.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # harnesses/kimi-code
RUNNER = ROOT / "runner" / "runner.py"


def run_runner(*argv):
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, timeout=120)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def make_task(tmp, **overrides):
    task = {"task_id": "t1", "prompt": "Fix the bug.", "features": {},
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


class TaskSchemaGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-schema-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a12_string_boolean_feature_refused_at_the_boundary(self):
        # A12 (#83): "false" was truthy at the lane table; the task went to
        # the flash lane and its cheap worker. The boundary must refuse it.
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp, features={"bounded": "false",
                                             "known_location": True,
                                             "objective_acceptance": True})
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 3, out)
        self.assertEqual(out["error"], "task_schema_invalid")
        self.assertEqual(out["field"], "features.bounded")

    def test_unknown_lane_override_refused(self):
        # A typo'd lane id must not flow into dispatch's agent lookup.
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp, lane="prod")
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 3, out)
        self.assertEqual(out["error"], "task.lane_not_a_lane")

    def test_valid_task_still_reaches_init(self):
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp, features={"bounded": True})
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)

    def test_flat_install_layout_resolves_the_schema(self):
        # Regression (2026-09-09): the eagerly-built candidates tuple indexed
        # parents[2] unconditionally, which crashed EVERY command in the flat
        # install (C:/Tools/model-proctor) where the runner dir has no
        # grandparent. Run the runner from a copied flat layout and expect a
        # normal lane decision, not a traceback.
        flat = Path(self.tmp) / "flat"
        flat.mkdir()
        shutil.copy2(RUNNER, flat / "runner.py")
        shutil.copy2(ROOT.parents[1] / "core" / "task_schema.py",
                     flat / "task_schema.py")
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp, features={
            "bounded": True, "known_location": True, "objective_acceptance": True})
        r = subprocess.run([sys.executable, str(flat / "runner.py"),
                            "lane", "--task", task],
                           capture_output=True, text=True, timeout=120)
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        self.assertEqual(r.returncode, 0, out)
        self.assertEqual(out["lane"], "flash")
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
