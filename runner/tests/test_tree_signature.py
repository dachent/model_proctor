#!/usr/bin/env python3
"""tree_signature() must bind content, not just git status letters (#38).

`git status --porcelain=v1` emits `XY PATH` and nothing else — no object name,
no content hash. A tracked file already reading ' M path' keeps a
byte-identical line when edited again, and HEAD does not move, so the old
signature could not see the mutation and cmd_accept's staleness check passed
on a tree that was never verified.

Existing coverage missed this entirely: test_runner.py's S3 uses
make_workspace(), a plain mkdtemp subdir with no `git init`, so it exercises
only the `files:` branch (which hashes contents and was always correct);
GitWorkspaceTest git-inits but never reaches verify or accept.

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
# Same behaviour as FIXED, different bytes — isolates content-blindness from
# any change in whether the verifier passes.
FIXED2 = ('def sum_to_n(n):\n    # same behaviour, different bytes\n'
          '    return sum(range(1, n + 1))\n')
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
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip() else {})


def git(ws, *args):
    return subprocess.run(["git", "-C", str(ws), *args],
                          capture_output=True, text=True, timeout=60)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-treesig-")

    def tearDown(self):
        # Git keeps objects read-only on Windows; ignore_errors is required.
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_task(self):
        task = {
            "task_id": "t1",
            "prompt": "Fix the bug.",
            "features": {"bounded": True, "known_location": True,
                         "objective_acceptance": True},
            "scope": ["math_utils.py"],
            "verifier": {"argv": ["{python}", "check.py"]},
            "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60},
        }
        p = Path(self.tmp) / "task.json"
        p.write_text(json.dumps(task), encoding="utf-8")
        return str(p)

    def _seed(self, ws):
        (ws / "math_utils.py").write_text(BUGGY, encoding="utf-8")
        (ws / "check.py").write_text(CHECK, encoding="utf-8")

    def make_git_ws(self):
        """A committed git repo whose root IS the workspace (#20)."""
        ws = Path(self.tmp) / "repo"
        ws.mkdir()
        self._seed(ws)
        git(ws, "init", "-q")
        git(ws, "config", "user.email", "t@t")
        git(ws, "config", "user.name", "t")
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "init")
        return str(ws)

    def make_plain_ws(self):
        ws = Path(self.tmp) / "ws"
        ws.mkdir()
        self._seed(ws)
        return str(ws)

    def dispatch_fix(self, ws, task, content=FIXED):
        return run_runner("dispatch", "--workspace", ws, "--task", task,
                          "--delegate", str(FAKE_WORKER),
                          env_extra={"FAKE_WORKER_WRITE": "math_utils.py",
                                     "FAKE_WORKER_CONTENT": content})

    def green_verify(self, ws, task):
        rc, ver = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertTrue(ver.get("passed"), ver)
        return ver


class GitTreeBindingTest(_Base):

    def test_tracked_dirty_file_reedit_stales_receipt(self):
        ws = self.make_git_ws()
        task = self.make_task()
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.dispatch_fix(ws, task)
        ver = self.green_verify(ws, task)
        self.assertTrue(ver["tree_sig"].startswith("git:"), ver["tree_sig"])

        # math_utils.py is ALREADY dirty (' M math_utils.py'). Re-editing it
        # leaves that porcelain line byte-identical and HEAD unchanged.
        (Path(ws) / "math_utils.py").write_text(FIXED2, encoding="utf-8")

        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1, f"accepted an unverified tree: {out}")
        self.assertIn("stale_receipt", out.get("reason", ""))

    def test_untracked_file_reedit_stales_receipt(self):
        ws = self.make_git_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch_fix(ws, task)
        # Present at verify, so it renders as '?? helper.py'; its content is
        # likewise absent from a status-only signature payload.
        (Path(ws) / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.green_verify(ws, task)

        (Path(ws) / "helper.py").write_text("VALUE = 999\n", encoding="utf-8")

        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1, f"accepted an unverified tree: {out}")
        self.assertIn("stale_receipt", out.get("reason", ""))

    def test_clean_tree_accepts(self):
        """The fix must not make every accept fail: no mutation, no refusal."""
        ws = self.make_git_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch_fix(ws, task)
        self.green_verify(ws, task)
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["accepted"])

    def test_signature_is_stable_across_repeated_calls(self):
        """Determinism: an unchanged tree must hash identically every time."""
        ws = self.make_git_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch_fix(ws, task)
        first = self.green_verify(ws, task)["tree_sig"]
        second = self.green_verify(ws, task)["tree_sig"]
        self.assertEqual(first, second)

    def test_unborn_head_is_handled(self):
        """git init with no commit has no HEAD; init must still work (#20)."""
        ws = Path(self.tmp) / "unborn"
        ws.mkdir()
        self._seed(ws)
        git(ws, "init", "-q")
        task = self.make_task()
        rc, out = run_runner("init", "--workspace", str(ws), "--task", task)
        self.assertEqual(rc, 0, out)

    def test_non_git_path_still_binds_content(self):
        """Regression guard: the files: manifest path was already correct."""
        ws = self.make_plain_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        self.dispatch_fix(ws, task)
        ver = self.green_verify(ws, task)
        self.assertTrue(ver["tree_sig"].startswith("files:"))
        (Path(ws) / "math_utils.py").write_text(FIXED2, encoding="utf-8")
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1)
        self.assertIn("stale_receipt", out.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
