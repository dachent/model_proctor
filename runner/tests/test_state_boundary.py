#!/usr/bin/env python3
"""State-root boundary hardening (#40).

Two tractable pieces of the tamper-evident-not-sealed problem:

  * cmd_init had no guard against existing state. Re-running it unconditionally
    re-sealed whatever was on disk NOW, re-snapshotted init_config_surface and
    init_tree_sig, and reset dispatches / failures / accepted. So a worker did
    not need to forge a receipt at all -- `runner.py init` made every added
    conftest.py baseline, every weakened check.py the sealed original, and
    refilled the dispatch budget. Re-initialisation is sometimes legitimate,
    so this is an explicit flag rather than a hard refusal, and it is counted
    on state so the laundering is at least visible.

  * nothing refused a --state-dir resolving INSIDE the workspace, which is the
    one configuration that fully collapses the boundary. STATE_DIR = ".runner"
    is a vestige of the old in-workspace design and is still pruned by
    _walk_files, so such a state root would also be invisible to
    config_surface() and to the non-git tree signature.

What this does NOT fix is recorded in #40: the state root remains at a
worker-computable path with no OS-level confinement, and the installed tool
directory is user-writable.

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


def run_runner(*argv, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=120)
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip() else {})


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-boundary-")

    def tearDown(self):
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

    def make_ws(self):
        ws = Path(self.tmp) / "ws"
        ws.mkdir()
        (ws / "math_utils.py").write_text(BUGGY, encoding="utf-8")
        (ws / "check.py").write_text(CHECK, encoding="utf-8")
        return str(ws)


class ReinitGuardTest(_Base):
    """init must not silently launder a tampered workspace into a new baseline."""

    def test_second_init_refused(self):
        ws = self.make_ws()
        task = self.make_task()
        self.assertEqual(run_runner("init", "--workspace", ws,
                                    "--task", task)[0], 0)
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1, f"re-init succeeded silently: {out}")
        self.assertEqual(out["error"], "already_initialized")

    def test_reinit_flag_allows_and_counts(self):
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        rc, out = run_runner("init", "--workspace", ws, "--task", task,
                             "--reinit")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["reinit_count"], 1)

        rc, out = run_runner("init", "--workspace", ws, "--task", task,
                             "--reinit")
        self.assertEqual(out["reinit_count"], 2, "reinits must accumulate")

        rc, state = run_runner("status", "--workspace", ws)
        self.assertEqual(state["reinit_count"], 2)

    def test_reinit_after_tamper_is_visible(self):
        """The laundering path still works, but leaves a countable trace."""
        ws = self.make_ws()
        task = self.make_task()
        run_runner("init", "--workspace", ws, "--task", task)
        # Worker weakens the sealed verifier, then re-baselines it.
        (Path(ws) / "check.py").write_text("print('PASS')\n", encoding="utf-8")
        rc, out = run_runner("init", "--workspace", ws, "--task", task,
                             "--reinit")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["reinit_count"], 1)

    def test_first_init_unaffected(self):
        ws = self.make_ws()
        task = self.make_task()
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.get("reinit_count", 0), 0)


class StateDirBoundaryTest(_Base):
    """--state-dir must not be talked into the agent-writable tree."""

    def test_state_dir_inside_workspace_refused(self):
        ws = self.make_ws()
        task = self.make_task()
        inside = str(Path(ws) / ".runner")
        rc, out = run_runner("init", "--workspace", ws, "--task", task,
                             "--state-dir", inside)
        self.assertEqual(rc, 1, f"state root accepted inside workspace: {out}")
        self.assertEqual(out["error"], "state_dir_inside_workspace")

    def test_state_dir_equal_to_workspace_refused(self):
        ws = self.make_ws()
        task = self.make_task()
        rc, out = run_runner("init", "--workspace", ws, "--task", task,
                             "--state-dir", ws)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["error"], "state_dir_inside_workspace")

    def test_state_dir_outside_workspace_accepted(self):
        ws = self.make_ws()
        task = self.make_task()
        outside = str(Path(self.tmp) / "external-state")
        rc, out = run_runner("init", "--workspace", ws, "--task", task,
                             "--state-dir", outside)
        self.assertEqual(rc, 0, out)
        self.assertTrue((Path(outside) / "state.json").is_file())

    def test_full_loop_survives_external_state_dir(self):
        """The refusal must not break the legitimate override path."""
        ws = self.make_ws()
        task = self.make_task()
        outside = str(Path(self.tmp) / "external-state2")
        # (see LateralSwitchTest below for the other caller this guard broke)
        run_runner("init", "--workspace", ws, "--task", task,
                   "--state-dir", outside)
        run_runner("dispatch", "--workspace", ws, "--task", task,
                   "--state-dir", outside, "--delegate", str(FAKE_WORKER),
                   env_extra={"FAKE_WORKER_WRITE": "math_utils.py",
                              "FAKE_WORKER_CONTENT": FIXED})
        rc, ver = run_runner("verify", "--workspace", ws, "--task", task,
                             "--state-dir", outside)
        self.assertTrue(ver.get("passed"), ver)
        rc, out = run_runner("accept", "--workspace", ws, "--task", task,
                             "--state-dir", outside)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["accepted"])


class LateralSwitchTest(_Base):
    """pilot.py re-inits an initialized workspace on a lateral switch.

    The guard added here refuses that without --reinit, and pilot.py used to
    discard the return code -- so the lane would silently NOT change while
    summary["switched_to"] still claimed it had. The dispatch would run on the
    old lane and the evidence row would say otherwise: worse than a failure,
    because it falsifies the record rather than stopping.

    No hermetic test reaches pilot's real-dispatch path, which is the gap #54
    exists for. These two cover the capability and the call site.
    """

    def test_reinit_actually_changes_the_lane(self):
        ws = self.make_ws()
        task = self.make_task()
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        first_lane = out["lane"]

        p = Path(task)
        t = json.loads(p.read_text(encoding="utf-8"))
        t["lane"] = "k3" if first_lane != "k3" else "glm"
        p.write_text(json.dumps(t), encoding="utf-8")

        rc, out = run_runner("init", "--workspace", ws, "--task", task,
                             "--reinit")
        self.assertEqual(rc, 0, f"lateral switch blocked by the guard: {out}")
        self.assertEqual(out["lane"], t["lane"], "the lane must actually change")
        self.assertEqual(out["reinit_count"], 1)

    def test_pilot_passes_reinit_and_checks_the_result(self):
        src = (ROOT / "runner" / "pilot.py").read_text(encoding="utf-8")
        # Anchor on the branch itself, not the word -- it also appears in the
        # module docstring, and splitting on that grabs the whole file.
        anchor = 'recommendation.get("action") == "lateral_switch"'
        self.assertIn(anchor, src, "lateral-switch branch not found")
        switch = src.split(anchor, 1)[1].split("started = time.time()", 1)[0]
        self.assertLess(len(switch), 1500, "anchor matched too much")
        self.assertIn("--reinit", switch,
                      "pilot.py's lateral switch must pass --reinit")
        self.assertIn("rc_sw", switch,
                      "pilot.py must check the re-init return code, or a "
                      "blocked switch silently falsifies switched_to")


if __name__ == "__main__":
    unittest.main()
