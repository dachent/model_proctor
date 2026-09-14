#!/usr/bin/env python3
"""State-identity gate tests (#83 TOOL-030 M1 slice, A03/A04).

State has recorded task_id and workspace since the MVP, but no command
compared them: a different task file with the same verifier text, or a
--state-dir reused from a byte-identical workspace, was consumed silently.
These tests pin that every state-consuming boundary now refuses the
mismatch, and that init --reinit remains the legal identity change.
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

FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15\n'
    'print("PASS")\n'
)


def run_runner(*argv):
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, timeout=120)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def make_task(tmp, task_id, features=None):
    task = {"task_id": task_id, "prompt": "Fix the bug.",
            "features": features or {"bounded": True},
            "scope": ["math_utils.py"],
            "verifier": {"argv": ["{python}", "check.py"]},
            "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60}}
    p = Path(tmp) / f"task-{task_id}.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    return str(p)


def make_workspace(tmp, name="ws"):
    ws = Path(tmp) / name
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "math_utils.py").write_text(FIXED, encoding="utf-8")
    (ws / "check.py").write_text(CHECK, encoding="utf-8")
    return str(ws)


class StateIdentityGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-identity-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _init(self, ws, task):
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        return out

    def test_a03_wrong_task_with_reused_state_refused(self):
        # A03 (#83): same workspace, same verifier text, DIFFERENT task file.
        # Before the gate, verify+accept consumed task A's state for task B.
        ws = make_workspace(self.tmp)
        task_a = make_task(self.tmp, "t_a")
        self._init(ws, task_a)
        task_b = make_task(self.tmp, "t_b")   # identical except task_id
        for cmd in ("verify", "accept", "record", "dispatch"):
            argv = [cmd, "--workspace", ws, "--task", task_b]
            if cmd == "record":
                argv += ["--wire", str(Path(self.tmp) / "no-such-wire.jsonl")]
                # record without --wire skips usage; without wires it is fine,
                # so give it a wire path that does not exist is not it either —
                # record with no --wire exercises the identity gate first.
                argv = [cmd, "--workspace", ws, "--task", task_b]
            rc, out = run_runner(*argv)
            self.assertEqual(rc, 1, (cmd, out))
            self.assertEqual(out.get("error"), "state_task_mismatch", (cmd, out))
            self.assertEqual(out.get("state_task_id"), "t_a")
            self.assertEqual(out.get("task_id"), "t_b")

    def test_a04_wrong_workspace_with_reused_state_dir_refused(self):
        # A04 (#83): byte-identical clone of the workspace, --state-dir pointed
        # at the original's state root. Before the gate, the clone's tree was
        # verified and accepted against the original's pinned state.
        ws1 = make_workspace(self.tmp, "ws1")
        task_a = make_task(self.tmp, "t_a")
        out = self._init(ws1, task_a)
        sroot = out["state_dir"]
        ws2 = make_workspace(self.tmp, "ws2")   # same bytes, different path
        for cmd in ("verify", "accept"):
            rc, out2 = run_runner(cmd, "--workspace", ws2, "--task", task_a,
                                  "--state-dir", sroot)
            self.assertEqual(rc, 1, (cmd, out2))
            self.assertEqual(out2.get("error"), "state_workspace_mismatch", (cmd, out2))

    def test_reinit_is_the_legal_identity_change(self):
        ws = make_workspace(self.tmp)
        self._init(ws, make_task(self.tmp, "t_a"))
        rc, out = run_runner("init", "--workspace", ws,
                             "--task", make_task(self.tmp, "t_b"), "--reinit")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["task_id"], "t_b")
        self.assertEqual(out["reinit_count"], 1)
        # The new identity now verifies cleanly on the fixed tree.
        rc, out = run_runner("verify", "--workspace", ws,
                             "--task", make_task(self.tmp, "t_b"))
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.get("passed"))

    def test_same_identity_still_flows(self):
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp, "t_same")
        self._init(ws, task)
        rc, out = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertNotEqual(out.get("error"), "state_task_mismatch")


if __name__ == "__main__":
    unittest.main()
