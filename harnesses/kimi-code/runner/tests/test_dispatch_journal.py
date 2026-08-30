#!/usr/bin/env python3
"""Dispatch journal + status stall detector (#73, RCCA corrective C1/C2).

C1: `dispatch` journals a dispatch_open line BEFORE the delegate spawn and a
    dispatch_finished line after, append-only; a runner that dies mid-dispatch
    leaves the open entry behind, and the next dispatch flags entries past the
    delegate ceiling (timeout + 120s) as orphaned instead of losing them.
C2: `status` reports last-activity age, stall_suspected, open/orphaned journal
    entries, and the last receipt's dispatch_seq/nondiscriminating flag, so a
    resumed leader gets a dead-run verdict in one shot.
C3: a zero-dispatch or nondiscriminating green is visible on status without
    opening state.json / the receipt by hand.

Run: python -m unittest discover -s runner/tests -v
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "runner" / "runner.py"
FAKE_WORKER = Path(__file__).resolve().parent / "fake_worker.py"

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


def state_dir_for(ws):
    """Replicate runner._state_root's default layout for assertions."""
    ws = Path(ws).resolve()
    key = re.sub(r"[^A-Za-z0-9_.-]+", "_", ws.name)
    digest = hashlib.sha256(str(ws).encode("utf-8")).hexdigest()[:8]
    return ws.parent / ".runner-state" / f"{key}-{digest}"


def journal_lines(sdir):
    jr = Path(sdir) / "journal.jsonl"
    if not jr.is_file():
        return []
    return [json.loads(ln) for ln in jr.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


class DispatchJournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-journal-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _setup_ready(self):
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp)
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        return ws, task, Path(out["state_dir"])

    # ── C1/A1: journal pairs open/finished by dispatch_id ────────────────
    def test_journal_records_open_and_finished(self):
        ws, task, sdir = self._setup_ready()
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_WORKER))
        self.assertEqual(rc, 0, out)
        lines = [e for e in journal_lines(sdir)
                 if e["event"].startswith("dispatch")]
        self.assertEqual([e["event"] for e in lines],
                         ["dispatch_open", "dispatch_finished"])
        self.assertEqual(lines[0]["dispatch_seq"], 0)
        self.assertEqual(lines[0]["agent"], "glm-flash-worker")
        # A1: open and finished repeat the same minted dispatch_id.
        self.assertEqual(lines[0]["dispatch_id"], lines[1]["dispatch_id"])
        self.assertEqual(out["dispatch_id"], lines[0]["dispatch_id"])
        self.assertEqual(lines[1]["envelope_status"], "completed")

    # ── C1: a crashed runner's open entry is flagged, never silent ──────
    def test_orphaned_open_dispatch_is_surfaced(self):
        ws, task, sdir = self._setup_ready()
        # Hand-write the crash signature: dispatch_open, no finished pair,
        # started well past the delegate ceiling (timeout 60 + 120).
        started = time.strftime("%Y-%m-%dT%H:%M:%S",
                                time.localtime(time.time() - 7200))
        (sdir / "journal.jsonl").write_text(
            json.dumps({"journal_seq": 0, "event": "dispatch_open",
                        "task_id": "t1", "dispatch_id": "crash-id-1",
                        "dispatch_seq": 0,
                        "agent": "glm-worker", "timeout_s": 60,
                        "runner_pid": 999999, "at": started}) + "\n",
            encoding="utf-8")

        rc, out = run_runner("status", "--workspace", ws)
        self.assertEqual(rc, 0, out)
        self.assertIn("crash-id-1", out["orphaned_dispatch_ids"], out)

        # The next legal dispatch surfaces the orphan too, and still proceeds
        # (A6: advisory only — no refusal, no non-zero exit).
        env = {"FAKE_WORKER_WRITE": "math_utils.py", "FAKE_WORKER_CONTENT": FIXED}
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_WORKER), env_extra=env)
        self.assertEqual(rc, 0, out)
        self.assertIn("crash-id-1", out["journal_orphans"], out)
        seqs = [e["orphaned_dispatch_id"] for e in journal_lines(sdir)
                if e["event"] == "dispatch_orphaned"]
        self.assertEqual(seqs, ["crash-id-1"])

    # ── A6: an acked orphan stops re-reporting ──────────────────────────
    def test_orphan_ack_stops_reReporting(self):
        ws, task, sdir = self._setup_ready()
        started = time.strftime("%Y-%m-%dT%H:%M:%S",
                                time.localtime(time.time() - 7200))
        (sdir / "journal.jsonl").write_text(
            json.dumps({"journal_seq": 0, "event": "dispatch_open",
                        "task_id": "t1", "dispatch_id": "crash-id-1",
                        "dispatch_seq": 0, "agent": "glm-worker",
                        "timeout_s": 60, "runner_pid": 999999,
                        "at": started}) + "\n",
            encoding="utf-8")
        rc, out = run_runner("status", "--workspace", ws)
        self.assertIn("crash-id-1", out["orphaned_dispatch_ids"], out)
        rc, out = run_runner("journal", "--workspace", ws, "--ack", "crash-id-1")
        self.assertEqual(rc, 0, out)
        rc, out = run_runner("status", "--workspace", ws)
        self.assertEqual(out["orphaned_dispatch_ids"], [], out)

    # ── A1: a torn trailing line is reported, never fatal ───────────────
    def test_torn_tail_reported_not_fatal(self):
        ws, task, sdir = self._setup_ready()
        with open(sdir / "journal.jsonl", "a", encoding="utf-8") as f:
            f.write('{"event": "dispatch_open", "dispatch_id": "torn')  # no \n
        rc, out = run_runner("status", "--workspace", ws)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["journal_tail_corrupt"], out)

    # ── C1: finished entries close their opens, even on provider failure ─
    def test_timeout_leaves_a_finished_pair(self):
        ws, task, sdir = self._setup_ready()
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_WORKER),
                             env_extra={"FAKE_WORKER_MODE": "timeout"})
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["envelope_status"], "timeout")
        events = [e["event"] for e in journal_lines(sdir)
                  if e["event"].startswith("dispatch")]
        self.assertEqual(events, ["dispatch_open", "dispatch_finished"])
        self.assertEqual(journal_lines(sdir)[-1]["envelope_status"], "timeout")

    # ── C2: status is a one-shot dead-run detector ──────────────────────
    def test_status_stall_detector(self):
        ws, task, sdir = self._setup_ready()
        rc, out = run_runner("status", "--workspace", ws)
        self.assertEqual(rc, 0, out)
        self.assertFalse(out["stall_suspected"], out)
        self.assertEqual(out["open_journal_ids"], [])
        self.assertEqual(out["orphaned_dispatch_ids"], [])

        # Age every activity trace beyond the stall threshold.
        aged = time.time() - (out["stall_after_seconds"] + 600)
        os.utime(sdir / "state.json", (aged, aged))
        rc, out = run_runner("status", "--workspace", ws)
        self.assertTrue(out["stall_suspected"], out)
        self.assertGreater(out["seconds_since_last_event"],
                           out["stall_after_seconds"])

    # ── C3: the last receipt's verdict is visible without opening files ─
    def test_status_surfaces_last_receipt(self):
        ws, task, sdir = self._setup_ready()
        env = {"FAKE_WORKER_WRITE": "math_utils.py", "FAKE_WORKER_CONTENT": FIXED}
        run_runner("dispatch", "--workspace", ws, "--task", task,
                   "--delegate", str(FAKE_WORKER), env_extra=env)
        run_runner("verify", "--workspace", ws, "--task", task)
        rc, out = run_runner("status", "--workspace", ws)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["last_receipt"]["passed"], out)
        self.assertEqual(out["last_receipt"]["dispatch_seq"], 1)
        # Back-compat: raw state keys stay readable at top level.
        self.assertEqual(out["reinit_count"], 0)
        self.assertIn("budget", out)


    # ── A3: a zero-dispatch nondiscriminating green refuses by default ──
    def test_zero_dispatch_accept_is_gated(self):
        ws = Path(make_workspace(self.tmp))
        (ws / "math_utils.py").write_text(FIXED, encoding="utf-8")
        task = make_task(self.tmp)
        rc, out = run_runner("init", "--workspace", str(ws), "--task", task)
        self.assertEqual(rc, 0, out)
        # No dispatches: verify is the baseline run and goes green on the
        # unmodified init tree — exactly the S4 receipt shape.
        rc, out = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["verifier_nondiscriminating"], out)
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertNotEqual(rc, 0)
        self.assertTrue(out["reason"].startswith(
            "zero_dispatch_nondiscriminating_receipt"), out)
        # A3: the reviewed-decision override works and is counted on state.
        rc, out = run_runner("accept", "--workspace", ws, "--task", task,
                             "--allow-zero-dispatch")
        self.assertEqual(rc, 0, out)
        rc, out = run_runner("status", "--workspace", ws)
        self.assertEqual(out["allow_zero_dispatch_count"], 1)


if __name__ == "__main__":
    unittest.main()
