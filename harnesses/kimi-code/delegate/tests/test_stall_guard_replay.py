#!/usr/bin/env python3
"""TOOL-035 (#106) acceptance replays: the five dated false-/true-kill
incidents from the issue body, replayed as scripted observation worlds
against run_escalation with fully injected clocks (no wall-clock sleeps).

When TOOL-039's journal-level replay harness lands, port these five
scenarios to it as fixtures; until then this file IS the replay gate.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_DELEGATE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DELEGATE_DIR))

import stall_guard  # noqa: E402


class ReplayWorld:
    """Virtual clock + scripted payload for incident replays."""

    def __init__(self, run_dir):
        self.t = 0.0
        self.run_dir = run_dir
        self.hb_path = os.path.join(run_dir, stall_guard.HEARTBEAT_FILE)
        self.alive = True
        self.capture_frames = None   # None = capture channel dead
        self.observations_live = True
        self._beat = 0

    def monotonic(self):
        return self.t

    def wall(self):
        return self.t

    def sleep(self, d):
        self.t += d
        self._maybe_answer_capture()

    def proc_alive(self):
        return self.alive

    # ── scripted payload behaviors ────────────────────────────────────
    def heartbeat(self, items_done):
        """Emit one heartbeat and stamp the file mtime at virtual now."""
        self._beat += 1
        with open(self.hb_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"beat": self._beat, "epoch": self.t,
                                "counters": {"items_done": items_done}}) + "\n")
        os.utime(self.hb_path, (self.t, self.t))

    def advance_with_progress(self, seconds):
        """Live payload: advancing progress, fresh heartbeats."""
        steps = max(1, int(seconds))
        for _ in range(steps):
            self.t += seconds / steps
            self._beat += 1
            self.heartbeat(self._beat)
            self._maybe_answer_capture()

    def _maybe_answer_capture(self):
        frames = self.capture_frames
        if frames is None or not self.observations_live:
            return
        req = os.path.join(self.run_dir, stall_guard.STACK_REQUEST_FILE)
        if not os.path.exists(req):
            return
        try:
            with open(req, encoding="utf-8") as f:
                seq = json.load(f)["seq"]
        except (OSError, json.JSONDecodeError):
            return
        with open(os.path.join(self.run_dir,
                               stall_guard.STACK_CAPTURE_FMT.format(seq=seq)),
                  "w", encoding="utf-8") as f:
            json.dump({"seq": seq, "captured_at": self.t,
                       "frames": frames}, f)


def _policy():
    return stall_guard.StallPolicy.from_config(
        {"enabled": True, "heartbeat_stale_after_s": 20,
         "capture_interval_s": 10, "capture_timeout_s": 5,
         "max_extensions": 1})


def _replay(world):
    return stall_guard.run_escalation(
        world.hb_path, _policy(), proc_alive=world.proc_alive,
        sleep=world.sleep, monotonic=world.monotonic, wall=world.wall)


class IncidentReplayTest(unittest.TestCase):

    def setUp(self):
        self.run_dir = tempfile.mkdtemp(prefix="sg-replay-")
        self.addCleanup(shutil.rmtree, self.run_dir, ignore_errors=True)

    def _assert_no_kill(self, verdict):
        self.assertEqual(verdict.action, "completed_race")
        self.assertIsNone(verdict.kill_reason)
        self.assertIsNone(verdict.evidence["kill_reason"])

    # 2026-09-01 (x2): killed minutes AFTER the job had succeeded.
    def test_replay_2026_09_01_attempts_a_and_b(self):
        for attempt in ("A", "B"):
            with self.subTest(attempt=attempt):
                w = ReplayWorld(self.run_dir)
                w.alive = False  # the payload had already succeeded
                v = _replay(w)
                # The ladder burns at most one capture timeout discovering
                # the exit, then reports the race — never a kill.
                self._assert_no_kill(v)

    # 2026-09-12: killed 46 s after the final workbook succeeded. The work
    # was done; under the delegate the exiting process must read as
    # completed, not as a stall. Modelled deterministically: the child exits
    # during the ladder's first interval wait (any exit point between ladder
    # entry and conviction is the same class; the wait-loop poll covers
    # exits before entry).
    def test_replay_2026_09_12(self):
        w = ReplayWorld(self.run_dir)
        w.capture_frames = ["excel.py:42:save"]
        w.heartbeat(items_done=7)
        real_sleep = w.sleep
        def exit_during_first_interval(d):
            real_sleep(d)
            if w.t >= 3.0:
                w.alive = False
        w.sleep = exit_during_first_interval
        v = _replay(w)
        self._assert_no_kill(v)

    # 2026-09-16/17: kills on corrupted measurements (observer-side).
    def test_replay_2026_09_16_17(self):
        w = ReplayWorld(self.run_dir)
        # Pass 1: observation channel dead (the monitor's own stat hung).
        w.observations_live = False
        w.capture_frames = ["etl.py:10:run"]
        recovered = {"done": False}
        real_sleep = w.sleep
        def recover_and_finish(d):
            real_sleep(d)
            if not recovered["done"] and w.t >= 25.0:
                # Measurement recovers; the payload is healthy and working.
                recovered["done"] = True
                w.observations_live = True
                w.advance_with_progress(5)
            if w.t >= 40.0:
                # The run completes during the second pass's interval wait —
                # before any conviction, so the verdict must be the race.
                w.alive = False
        w.sleep = recover_and_finish
        v = _replay(w)
        self._assert_no_kill(v)
        # The corrupted first pass is on record as an abstain, not a verdict.
        self.assertIn(v.evidence["predicate"]["abstain"],
                      ("unobservable_payload", "measurement_incomplete", None))

    # 2026-09-25 attempts 06/07: genuine COM wedge — the correct kill.
    def test_replay_2026_09_25_genuine_wedge_kills_with_stack_evidence(self):
        w = ReplayWorld(self.run_dir)
        # Heartbeats flowed, then the event loop wedged: file goes stale,
        # progress frozen; the capture handler answers with the identical
        # frozen frame list.
        w.capture_frames = ["excel.py:88:com_call", "worker.py:12:run"]
        w.heartbeat(items_done=6)
        w.t += 120.0  # stale: no heartbeat for two minutes
        stale_path = os.path.join(self.run_dir, stall_guard.HEARTBEAT_FILE)
        os.utime(stale_path, (w.t - 120.0, w.t - 120.0))
        v = _replay(w)
        self.assertEqual(v.action, "kill")
        self.assertEqual(v.kill_reason, "condition_met_stall")
        pred = v.evidence["predicate"]
        self.assertEqual(pred, {"heartbeat_stale": True,
                                "progress_flat": True,
                                "stacks_identical": True, "abstain": None})
        caps = v.evidence["stack_captures"]
        self.assertEqual(len(caps), 2)
        self.assertEqual(caps[0]["signature"], caps[1]["signature"])
        self.assertTrue(all(c["ok"] for c in caps))


if __name__ == "__main__":
    unittest.main()
