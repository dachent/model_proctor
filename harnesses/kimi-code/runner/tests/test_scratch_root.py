#!/usr/bin/env python3
"""#115: the dispatch scratch root — one structured location, and a sweep that
actually runs.

Runner dispatches used to write the prompt task file into the system %TEMP%
(NamedTemporaryFile, delete=False) and to leave every isolated KIMI_CODE_HOME
behind: the caller-side sweep lived only in pilot.py, so a runner-only week
piled up 110 delegate-* run dirs and 114 delegate-kimi-home-* homes (~450 MB)
on the operator's workstation, plus lingering tmp*.txt task files.

The new contract these tests pin:
  * every per-dispatch artifact lives under MODEL_PROCTOR_SCRATCH
    (default C:\\Dev\\scratch\\model-proctor) as <root>/runs/<dispatch-id>,
    <root>/homes/<dispatch-id>, and <root>/taskfiles/<uuid>.txt;
  * the prompt task file is removed when the dispatch ends;
  * every dispatch start sweeps the root, and the sweep NEVER removes a home a
    live resume-handoff could still use — the current dispatch's home, and
    every home the current task's dispatches reference, are kept.

Run: python -m unittest discover -s runner/tests -v
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

ROOT = Path(__file__).resolve().parents[2]
RUNNER_DIR = ROOT / "runner"
RUNNER = RUNNER_DIR / "runner.py"
FAKE_WORKER = Path(__file__).resolve().parent / "fake_worker.py"

sys.path.insert(0, str(RUNNER_DIR))
import runner  # noqa: E402

BUGGY = 'def sum_to_n(n):\n    return sum(range(1, n))\n'
FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15, f"sum_to_n(5)={sum_to_n(5)}, expected 15"\n'
    'print("PASS")\n'
)

DAY_S = 24 * 3600


def run_runner(*argv, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=120)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def make_task(tmp, task_id="t1"):
    task = {
        "task_id": task_id,
        "prompt": "Fix the bug.",
        "features": {"bounded": True, "known_location": True,
                     "objective_acceptance": True},
        "scope": ["math_utils.py"],
        "verifier": {"argv": ["{python}", "check.py"]},
        "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60},
    }
    p = Path(tmp) / "task.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    return str(p)


def make_workspace(tmp):
    ws = Path(tmp) / "ws"
    ws.mkdir()
    (ws / "math_utils.py").write_text(BUGGY, encoding="utf-8")
    (ws / "check.py").write_text(CHECK, encoding="utf-8")
    return str(ws)


class ScratchRootTest(unittest.TestCase):
    """The root's resolution and the sweep's rules, in-process."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-scratch-")
        self.root = Path(self.tmp) / "scratch"
        self.root.mkdir()
        self._old = os.environ.get("MODEL_PROCTOR_SCRATCH")
        os.environ["MODEL_PROCTOR_SCRATCH"] = str(self.root)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("MODEL_PROCTOR_SCRATCH", None)
        else:
            os.environ["MODEL_PROCTOR_SCRATCH"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _entry(self, kind, name, age_s=0):
        p = self.root / kind / name
        if kind == "homes":
            p.mkdir(parents=True)
            (p / "sessions").mkdir()
        elif kind == "runs":
            p.mkdir(parents=True)
            (p / "stdout.log").write_text("x", encoding="utf-8")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")
        if age_s:
            stamp = time.time() - age_s
            os.utime(p, (stamp, stamp))
        return p

    # ── the root ────────────────────────────────────────────────────────
    def test_env_override_is_the_root(self):
        self.assertEqual(runner.scratch_root(), self.root)

    def test_default_root_is_the_policy_path(self):
        os.environ.pop("MODEL_PROCTOR_SCRATCH")
        self.assertEqual(runner.scratch_root(),
                         Path(r"C:\Dev\scratch\model-proctor"))
        self.assertEqual(runner.DEFAULT_SCRATCH_MAX_AGE_S, 14 * DAY_S)

    # ── the sweep ───────────────────────────────────────────────────────
    def test_old_home_removed_fresh_home_kept(self):
        old = self._entry("homes", "delegate-kimi-home-old", age_s=15 * DAY_S)
        fresh = self._entry("homes", "delegate-kimi-home-fresh")
        result = runner.sweep_orphan_homes()
        self.assertEqual(result["removed"], ["delegate-kimi-home-old"])
        self.assertIn("delegate-kimi-home-fresh", result["kept"])
        self.assertFalse(old.exists())
        self.assertTrue(fresh.is_dir())

    def test_referenced_home_is_kept_even_when_old(self):
        """The current task's dispatches are the leader's metering sources —
        age alone must not remove one."""
        referenced = self._entry("homes", "referenced-dispatch", age_s=99 * DAY_S)
        result = runner.sweep_orphan_homes(keep=[str(referenced)])
        self.assertEqual(result["removed"], [])
        self.assertIn("referenced-dispatch", result["kept"])
        self.assertTrue(referenced.is_dir())

    def test_finished_dispatch_home_removed_regardless_of_age(self):
        finished = self._entry("homes", "finished-elsewhere")
        result = runner.sweep_orphan_homes(finished=["finished-elsewhere"])
        self.assertEqual(result["removed"], ["finished-elsewhere"])
        self.assertFalse(finished.exists())

    def test_finished_does_not_override_keep(self):
        both = self._entry("homes", "both")
        result = runner.sweep_orphan_homes(keep=[str(both)],
                                           finished=["both"])
        self.assertEqual(result["removed"], [])
        self.assertTrue(both.is_dir())

    def test_taskfiles_and_run_dirs_swept_by_age(self):
        old_task = self._entry("taskfiles", "old.txt", age_s=15 * DAY_S)
        fresh_task = self._entry("taskfiles", "fresh.txt")
        old_run = self._entry("runs", "delegate-old", age_s=15 * DAY_S)
        fresh_run = self._entry("runs", "delegate-fresh")
        result = runner.sweep_orphan_homes()
        self.assertEqual(sorted(result["removed"]),
                         ["delegate-old", "old.txt"])
        self.assertFalse(old_task.exists())
        self.assertFalse(old_run.exists())
        self.assertTrue(fresh_task.is_file())
        self.assertTrue(fresh_run.is_dir())

    def test_missing_root_is_not_an_error(self):
        result = runner.sweep_orphan_homes(root=Path(self.tmp) / "nope")
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["errors"], [])

    def test_max_age_is_configurable(self):
        home = self._entry("homes", "recent", age_s=2 * DAY_S)
        self.assertEqual(runner.sweep_orphan_homes(max_age_s=7 * DAY_S)["removed"], [])
        self.assertEqual(runner.sweep_orphan_homes(max_age_s=DAY_S)["removed"],
                         ["recent"])
        self.assertFalse(home.exists())


class DispatchScratchTest(unittest.TestCase):
    """The same contract, end to end through a real runner dispatch."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-scratch-disp-")
        self.root = Path(self.tmp) / "scratch"
        self.ws = make_workspace(self.tmp)
        self.task = make_task(self.tmp)
        rc, out = run_runner("init", "--workspace", self.ws, "--task", self.task,
                             env_extra={"MODEL_PROCTOR_SCRATCH": str(self.root)})
        assert rc == 0, out
        self.state_dir = Path(out["state_dir"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dispatch(self, **env):
        return run_runner(
            "dispatch", "--workspace", self.ws, "--task", self.task,
            "--delegate", str(FAKE_WORKER),
            env_extra={"MODEL_PROCTOR_SCRATCH": str(self.root), **env})

    def _old_home(self, name):
        home = self.root / "homes" / name
        home.mkdir(parents=True)
        stamp = time.time() - 15 * DAY_S
        os.utime(home, (stamp, stamp))
        return home

    def test_task_file_is_written_under_the_root_and_deleted(self):
        recorded = Path(self.tmp) / "taskfile-path.txt"
        rc, out = self._dispatch(FAKE_WORKER_RECORD_TASKFILE=str(recorded))
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["envelope_status"], "completed")
        task_file = Path(recorded.read_text(encoding="utf-8"))
        self.assertEqual(task_file.parent, self.root / "taskfiles")
        self.assertEqual(task_file.suffix, ".txt")
        # Removed when the dispatch ended (the finally block), so nothing is
        # left for the next sweep to find.
        self.assertFalse(task_file.exists())
        self.assertEqual(list((self.root / "taskfiles").iterdir()), [])

    def test_dispatch_start_sweeps_old_homes_and_keeps_fresh_ones(self):
        old = self._old_home("delegate-kimi-home-stale")
        fresh = self.root / "homes" / "delegate-kimi-home-fresh"
        fresh.mkdir(parents=True)

        rc, out = self._dispatch()
        self.assertEqual(rc, 0, out)
        sweep = out["scratch_sweep"]
        self.assertEqual(sweep["root"], str(self.root))
        self.assertEqual(sweep["removed"], ["delegate-kimi-home-stale"])
        self.assertIn("delegate-kimi-home-fresh", sweep["kept"])
        self.assertFalse(old.exists())
        self.assertTrue(fresh.is_dir())

    def test_dispatch_keeps_a_home_the_current_task_references(self):
        """The constraint that matters: a home this task's dispatches reference
        is the leader's metering source — age alone must not remove it."""
        referenced = self._old_home("prior-dispatch")
        state_path = self.state_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["dispatches"].append({
            "agent": "glm-flash-worker", "status": "completed",
            "dispatch_id": "prior-dispatch", "detached": False,
            "child_home": str(referenced), "child_session_id": "s-1",
        })
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

        rc, out = self._dispatch()
        self.assertEqual(rc, 0, out)
        sweep = out["scratch_sweep"]
        self.assertEqual(sweep["removed"], [])
        self.assertIn("prior-dispatch", sweep["kept"])
        self.assertTrue(referenced.is_dir())

    def test_dispatch_sweeps_a_finished_dispatch_home_of_another_task(self):
        """A dispatch this state dir already recorded as finished (for another
        task) can be continued by nobody, so its home goes at any age."""
        home = self.root / "homes" / "finished-elsewhere"
        home.mkdir(parents=True)
        with open(self.state_dir / "journal.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "dispatch_finished", "dispatch_id": "finished-elsewhere",
                "task_id": "a-previous-task", "envelope_status": "completed",
            }) + "\n")

        rc, out = self._dispatch()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["scratch_sweep"]["removed"], ["finished-elsewhere"])
        self.assertFalse(home.exists())

    def test_unwritable_scratch_root_refuses_cleanly(self):
        blocker = Path(self.tmp) / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        rc, out = self._dispatch(MODEL_PROCTOR_SCRATCH=str(blocker / "sub"))
        self.assertEqual(rc, 4, out)
        self.assertEqual(out["error"], "scratch_root_unwritable")
        self.assertIn("MODEL_PROCTOR_SCRATCH", out["hint"])


if __name__ == "__main__":
    unittest.main()
