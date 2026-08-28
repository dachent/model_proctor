#!/usr/bin/env python3
"""The exam must be the one the leader set, and it must actually run (#37).

Two independent routes to a green receipt without the verifier doing its job:

  * the verifier argv was re-read from task.json on every command. That file
    lives in the agent-writable workspace, is not a config-surface name, is not
    auto-sealed, and was recorded nowhere in state -- so a worker could swap
    the exam for `print('ok')`. Sealing alone cannot fix it either, because
    load_task() runs BEFORE the restore loop, so the tampered argv is what
    executes this run;
  * `python -m MOD` puts the process cwd at sys.path[0] and cmd_verify runs
    with cwd=ws, so ws/MOD.py resolves ahead of the real module. Measured: an
    honest `python -m unittest discover` exits 1 on broken code; adding a
    one-line ws/unittest.py makes the same command exit 0 with an
    empty-string output hash. Neither unittest.py nor pytest.py is in
    CONFIG_SURFACE_NAMES and a -m argument is not a workspace file, so
    seal_files() never covered it and nothing fired.

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

BUGGY = 'def sum_to_n(n):\n    return sum(range(1, n))\n'
FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15, f"sum_to_n(5)={sum_to_n(5)}, expected 15"\n'
    'print("PASS")\n'
)
TEST_MODULE = (
    'import os, sys, unittest\n'
    'sys.path.insert(0, os.path.dirname(os.path.dirname('
    'os.path.abspath(__file__))))\n'
    'from math_utils import sum_to_n\n'
    'class T(unittest.TestCase):\n'
    '    def test_v(self):\n'
    '        self.assertEqual(sum_to_n(5), 15)\n'
)


def run_runner(*argv, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=120)
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip() else {})


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-verifier-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_task(self, verifier=None, seal=None):
        task = {
            "task_id": "t1",
            "prompt": "Fix the bug.",
            "features": {"bounded": True, "known_location": True,
                         "objective_acceptance": True},
            "scope": ["math_utils.py"],
            "verifier": verifier or {"argv": ["{python}", "check.py"]},
            "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60},
        }
        if seal is not None:
            task["seal"] = seal
        p = Path(self.tmp) / "task.json"
        p.write_text(json.dumps(task), encoding="utf-8")
        return str(p)

    def make_ws(self, with_tests=False):
        ws = Path(self.tmp) / "ws"
        ws.mkdir()
        (ws / "math_utils.py").write_text(BUGGY, encoding="utf-8")
        (ws / "check.py").write_text(CHECK, encoding="utf-8")
        if with_tests:
            t = ws / "tests"
            t.mkdir()
            (t / "__init__.py").write_text("", encoding="utf-8")
            (t / "test_sum.py").write_text(TEST_MODULE, encoding="utf-8")
        return str(ws)

    def dispatch(self, ws, task, content=FIXED):
        return run_runner("dispatch", "--workspace", ws, "--task", task,
                          "--delegate", str(FAKE_WORKER),
                          env_extra={"FAKE_WORKER_WRITE": "math_utils.py",
                                     "FAKE_WORKER_CONTENT": content})

    def module_task(self):
        return self.make_task(
            verifier={"argv": ["{python}", "-m", "unittest", "discover",
                               "-s", "tests"]},
            seal=["check.py"])


class VerifierPinTest(_Base):
    """The verification contract is pinned at init, outside the workspace."""

    def test_verify_refuses_swapped_verifier(self):
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch(ws, task, content=BUGGY)   # code still broken

        p = Path(task)
        t = json.loads(p.read_text(encoding="utf-8"))
        t["verifier"] = {"argv": ["{python}", "-c", "print('ok')"]}
        p.write_text(json.dumps(t), encoding="utf-8")

        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1, f"ran a swapped verifier: {ver}")
        self.assertEqual(ver.get("rejected"), "verifier_changed_since_init")
        self.assertEqual(ver.get("pinned_argv"), ["{python}", "check.py"])

        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1)

    def test_unmodified_task_file_verifies_normally(self):
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch(ws, task)
        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, ver)
        self.assertTrue(ver["passed"])
        self.assertIsNone(ver.get("rejected"))

    def test_receipt_records_what_was_verified(self):
        """A green receipt must say WHAT was verified, not only which tree."""
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch(ws, task)
        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(ver["verifier_argv"], ["{python}", "check.py"])


class ModuleShadowTest(_Base):
    """`python -m MOD` resolves MOD from the workspace first."""

    def test_module_file_shadow_rejected(self):
        ws = self.make_ws(with_tests=True)
        task = self.module_task()
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)

        # Worker leaves the bug in place and shadows the verifier module.
        self.dispatch(ws, task, content=BUGGY)
        (Path(ws) / "unittest.py").write_text("raise SystemExit(0)\n",
                                              encoding="utf-8")

        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1, f"broken code accepted via module shadow: {ver}")
        self.assertEqual(ver.get("rejected"), "module_shadow_detected")
        self.assertIn("unittest.py", json.dumps(ver.get("shadowed", [])))

    def test_package_shadow_rejected(self):
        """A directory package shadows just as well as a module file."""
        ws = self.make_ws(with_tests=True)
        task = self.module_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch(ws, task, content=BUGGY)
        pkg = Path(ws) / "unittest"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("raise SystemExit(0)\n",
                                         encoding="utf-8")

        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1, f"broken code accepted via package shadow: {ver}")
        self.assertEqual(ver.get("rejected"), "module_shadow_detected")

    def test_clean_module_verifier_gates_honestly(self):
        """No shadow: a -m verifier fails on broken code and passes on fixed."""
        ws = self.make_ws(with_tests=True)
        task = self.module_task()
        run_runner("init", "--workspace", ws, "--task", task)

        self.dispatch(ws, task, content=BUGGY)
        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertFalse(ver.get("passed"), "broken code must fail")
        self.assertIsNone(ver.get("rejected"), ver)

        self.dispatch(ws, task, content=FIXED)
        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertTrue(ver.get("passed"), ver)

    def test_in_tree_module_target_is_not_a_shadow(self):
        """A verifier deliberately targeting an in-tree module must still run.

        The check fires only when the name ALSO resolves outside the
        workspace; a module that exists only in the tree is the intended
        target, not a hijack.
        """
        ws = self.make_ws()
        (Path(ws) / "proctor_local_check.py").write_text(
            'import sys, os\n'
            'sys.path.insert(0, os.getcwd())\n'
            'from math_utils import sum_to_n\n'
            'assert sum_to_n(5) == 15\n'
            'print("PASS")\n', encoding="utf-8")
        task = self.make_task(
            verifier={"argv": ["{python}", "-m", "proctor_local_check"]},
            seal=["proctor_local_check.py"])
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.dispatch(ws, task)
        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertIsNone(ver.get("rejected"), ver)
        self.assertTrue(ver.get("passed"), ver)


if __name__ == "__main__":
    unittest.main()
