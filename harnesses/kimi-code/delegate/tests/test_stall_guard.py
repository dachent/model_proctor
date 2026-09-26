#!/usr/bin/env python3
"""TOOL-035 (#106): stall-guard policy, readers, and ladder tests."""

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


class PolicyTest(unittest.TestCase):

    def test_none_block_yields_disabled_defaults(self):
        p = stall_guard.StallPolicy.from_config(None)
        self.assertFalse(p.enabled)
        self.assertEqual(p.heartbeat_stale_after_s, 20.0)
        self.assertEqual(p.capture_interval_s, 10.0)
        self.assertEqual(p.capture_timeout_s, 5.0)
        self.assertEqual(p.max_extensions, 1)

    def test_defaults_sit_exactly_at_worst_case_cap(self):
        p = stall_guard.StallPolicy.from_config({"enabled": True})
        self.assertEqual(p.worst_case_added_s(), 60.0)
        self.assertLessEqual(p.worst_case_added_s(),
                             stall_guard.MAX_ADDED_SECONDS)

    def test_worst_case_overflow_rejected_when_enabled(self):
        with self.assertRaises(stall_guard.PolicyError):
            stall_guard.StallPolicy.from_config(
                {"enabled": True, "max_extensions": 3})

    def test_worst_case_overflow_allowed_when_disabled(self):
        p = stall_guard.StallPolicy.from_config(
            {"enabled": False, "max_extensions": 99})
        self.assertFalse(p.enabled)

    def test_unknown_keys_rejected(self):
        with self.assertRaises(stall_guard.PolicyError):
            stall_guard.StallPolicy.from_config({"enabled": True, "bogus": 1})

    def test_bool_rejected_where_number_expected(self):
        for key in ("heartbeat_stale_after_s", "capture_interval_s",
                    "capture_timeout_s"):
            with self.subTest(key=key):
                with self.assertRaises(stall_guard.PolicyError):
                    stall_guard.StallPolicy.from_config({key: True})

    def test_non_finite_and_subsecond_knobs_rejected(self):
        for bad in (float("inf"), float("nan"), 0.5, 0, -3):
            with self.subTest(bad=bad):
                with self.assertRaises(stall_guard.PolicyError):
                    stall_guard.StallPolicy.from_config(
                        {"capture_interval_s": bad})

    def test_enabled_must_be_bool(self):
        with self.assertRaises(stall_guard.PolicyError):
            stall_guard.StallPolicy.from_config({"enabled": 1})

    def test_max_extensions_must_be_non_negative_int(self):
        for bad in (-1, 1.5, True, "2"):
            with self.subTest(bad=bad):
                with self.assertRaises(stall_guard.PolicyError):
                    stall_guard.StallPolicy.from_config(
                        {"max_extensions": bad})


class SignatureTest(unittest.TestCase):

    def test_progress_signature_is_order_independent_and_stable(self):
        a = stall_guard.progress_signature({"phase": "run", "done": 3})
        b = stall_guard.progress_signature({"done": 3, "phase": "run"})
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)

    def test_progress_signature_changes_with_counters(self):
        a = stall_guard.progress_signature({"done": 3})
        b = stall_guard.progress_signature({"done": 4})
        self.assertNotEqual(a, b)

    def test_stack_signature_order_sensitive(self):
        a = stall_guard.stack_signature(["a.py:1:f", "b.py:2:g"])
        b = stall_guard.stack_signature(["b.py:2:g", "a.py:1:f"])
        self.assertNotEqual(a, b)

    def test_stack_signature_identical_frames_match(self):
        frames = ["compile.py:10:run", "cli.py:3:<module>"]
        self.assertEqual(stall_guard.stack_signature(frames),
                         stall_guard.stack_signature(list(frames)))


class ReaderTest(unittest.TestCase):

    def setUp(self):
        self.run_dir = tempfile.mkdtemp(prefix="sg-run-")
        self.addCleanup(shutil.rmtree, self.run_dir, ignore_errors=True)
        self.hb_path = os.path.join(self.run_dir, stall_guard.HEARTBEAT_FILE)

    def _write_heartbeat(self, records, mtime=None):
        with open(self.hb_path, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        if mtime is not None:
            os.utime(self.hb_path, (mtime, mtime))
        return self.hb_path

    def test_missing_heartbeat_file(self):
        r = stall_guard.read_heartbeat(self.hb_path, now=1000.0)
        self.assertFalse(r.seen)
        self.assertIsNone(r.mtime_age_s)

    def test_newest_record_wins_and_age_from_mtime(self):
        self._write_heartbeat(
            [{"beat": 1, "epoch": 999.0, "counters": {"done": 1}},
             {"beat": 2, "epoch": 1.0, "counters": {"done": 2}}],
            mtime=900.0)
        r = stall_guard.read_heartbeat(self.hb_path, now=1000.0)
        self.assertTrue(r.seen)
        self.assertEqual(r.mtime_age_s, 100.0)
        self.assertEqual(r.progress, {"done": 2})

    def test_payload_epoch_field_is_ignored_for_staleness(self):
        # epoch claims the far future; mtime is stale -> stale.
        self._write_heartbeat(
            [{"beat": 1, "epoch": 9e9, "counters": {}}], mtime=100.0)
        r = stall_guard.read_heartbeat(self.hb_path, now=1000.0)
        self.assertEqual(r.mtime_age_s, 900.0)

    def test_future_mtime_clamps_to_fresh(self):
        self._write_heartbeat([{"beat": 1, "epoch": 0, "counters": {}}],
                              mtime=5000.0)
        r = stall_guard.read_heartbeat(self.hb_path, now=1000.0)
        self.assertEqual(r.mtime_age_s, 0.0)

    def test_torn_tail_tolerated_and_flagged(self):
        path = self._write_heartbeat(
            [{"beat": 1, "epoch": 0, "counters": {"done": 7}}])
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"beat": 2, "epo')  # writer died mid-append
        r = stall_guard.read_heartbeat(self.hb_path)
        self.assertTrue(r.seen)
        self.assertTrue(r.torn_tail)
        self.assertEqual(r.progress, {"done": 7})

    def test_heartbeat_read_is_tail_bounded(self):
        # 300k records (~9 MB): the reader must return the newest record
        # without scanning the whole file.
        with open(self.hb_path, "w", encoding="utf-8") as f:
            for i in range(300000):
                f.write(json.dumps({"beat": i, "epoch": 0,
                                    "counters": {"done": i}}) + "\n")
        r = stall_guard.read_heartbeat(self.hb_path)
        self.assertTrue(r.seen)
        self.assertEqual(r.progress, {"done": 299999})

    def test_capture_roundtrip(self):
        clock = [100.0]
        def responder(d):
            clock[0] += d
            req = os.path.join(self.run_dir, stall_guard.STACK_REQUEST_FILE)
            if os.path.exists(req):
                with open(req, encoding="utf-8") as f:
                    seq = json.load(f)["seq"]
                with open(os.path.join(
                        self.run_dir,
                        stall_guard.STACK_CAPTURE_FMT.format(seq=seq)),
                        "w", encoding="utf-8") as f:
                    json.dump({"seq": seq, "captured_at": clock[0],
                               "frames": ["a.py:1:f", "b.py:2:g"]}, f)
        cap = stall_guard.request_stack_capture(
            self.run_dir, 1, 5.0, sleep=responder,
            monotonic=lambda: clock[0], now=clock[0])
        self.assertTrue(cap.ok)
        self.assertEqual(cap.seq, 1)
        self.assertEqual(cap.frames_count, 2)
        self.assertEqual(cap.signature,
                         stall_guard.stack_signature(["a.py:1:f", "b.py:2:g"]))
        # request file was written atomically: no tmp left behind
        self.assertFalse(os.path.exists(
            os.path.join(self.run_dir, ".stack_request_1.tmp")))

    def test_capture_timeout_returns_not_ok(self):
        clock = [0.0]
        cap = stall_guard.request_stack_capture(
            self.run_dir, 3, 1.0,
            sleep=lambda d: clock.__setitem__(0, clock[0] + d),
            monotonic=lambda: clock[0])
        self.assertFalse(cap.ok)
        self.assertIsNone(cap.signature)
        self.assertGreaterEqual(clock[0], 1.0)

    def test_capture_seq_mismatch_rejected(self):
        with open(os.path.join(self.run_dir, "stack_capture_9.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"seq": 8, "frames": ["x"]}, f)  # stale/wrong seq
        self.assertIsNone(stall_guard.read_stack_capture(self.run_dir, 9))

    def test_oversized_capture_rejected(self):
        with open(os.path.join(self.run_dir, "stack_capture_1.json"),
                  "w", encoding="utf-8") as f:
            f.write(" " * (stall_guard._MAX_CAPTURE_BYTES + 1))
        self.assertIsNone(stall_guard.read_stack_capture(self.run_dir, 1))


class FakeWorld:
    """Scripted clock + scripted payload for run_escalation tests."""

    def __init__(self, run_dir):
        self.t = 1000.0
        self.run_dir = run_dir
        self.hb_path = os.path.join(run_dir, stall_guard.HEARTBEAT_FILE)
        self.alive = True
        self.interrupted = False
        self.capture_frames = None   # set to a list to auto-answer captures

    def monotonic(self):
        return self.t

    def wall(self):
        return self.t

    def sleep(self, d):
        self.t += d
        self._maybe_answer_capture()

    def proc_alive(self):
        return self.alive

    def is_interrupted(self):
        return self.interrupted

    def _maybe_answer_capture(self):
        frames = self.capture_frames
        if frames is None:
            return
        req = os.path.join(self.run_dir, stall_guard.STACK_REQUEST_FILE)
        if not os.path.exists(req):
            return
        try:
            with open(req, encoding="utf-8") as f:
                seq = json.load(f)["seq"]
        except (OSError, json.JSONDecodeError):
            return
        if callable(frames):
            frames = frames(seq)
        with open(os.path.join(self.run_dir,
                               stall_guard.STACK_CAPTURE_FMT.format(seq=seq)),
                  "w", encoding="utf-8") as f:
            json.dump({"seq": seq, "captured_at": self.t,
                       "frames": frames}, f)

    def write_heartbeat(self, progress, age_s):
        with open(self.hb_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"beat": 1, "epoch": self.t - age_s,
                                "counters": progress}) + "\n")
        past = self.t - age_s
        os.utime(self.hb_path, (past, past))  # mtime drives staleness


def _policy(**kw):
    base = {"enabled": True, "heartbeat_stale_after_s": 5.0,
            "capture_interval_s": 2.0, "capture_timeout_s": 3.0,
            "max_extensions": 1}
    base.update(kw)
    return stall_guard.StallPolicy.from_config(base)


def _run(world, policy):
    return stall_guard.run_escalation(
        world.hb_path, policy, proc_alive=world.proc_alive,
        interrupted=world.is_interrupted, sleep=world.sleep,
        monotonic=world.monotonic, wall=world.wall)


class LadderTest(unittest.TestCase):

    def setUp(self):
        self.run_dir = tempfile.mkdtemp(prefix="sg-ladder-")
        self.addCleanup(shutil.rmtree, self.run_dir, ignore_errors=True)

    def test_all_conjuncts_true_kills_with_evidence(self):
        w = FakeWorld(self.run_dir)
        w.capture_frames = ["compile.py:10:run", "cli.py:3:<module>"]
        w.write_heartbeat({"done": 0}, age_s=100.0)  # stale, frozen
        v = _run(w, _policy())
        self.assertEqual(v.action, "kill")
        self.assertEqual(v.kill_reason, "condition_met_stall")
        self.assertEqual(v.evidence["predicate"],
                         {"heartbeat_stale": True, "progress_flat": True,
                          "stacks_identical": True, "abstain": None})
        caps = v.evidence["stack_captures"]
        self.assertEqual(len(caps), 2)
        self.assertTrue(all(c["ok"] for c in caps))
        self.assertEqual(caps[0]["signature"], caps[1]["signature"])
        self.assertEqual(v.evidence["extensions_used"], 0)
        self.assertEqual(v.evidence["kill_reason"], "condition_met_stall")
        self.assertTrue(v.evidence["evaluated_at"])

    def test_fresh_advancing_payload_extends_then_backstops(self):
        w = FakeWorld(self.run_dir)
        w.capture_frames = ["a.py:1:f"]
        w.write_heartbeat({"done": 1}, age_s=0.0)
        real_sleep = w.sleep
        def advancing(d):
            real_sleep(d)
            # progress advances on every wait; heartbeat re-stamped fresh
            w.write_heartbeat({"done": w.t}, age_s=0.0)
        w.sleep = advancing
        v = _run(w, _policy())
        self.assertEqual(v.action, "kill")
        self.assertEqual(v.kill_reason, "wall_clock_backstop")
        self.assertFalse(v.evidence["predicate"]["heartbeat_stale"])
        self.assertFalse(v.evidence["predicate"]["progress_flat"])
        self.assertIsNone(v.evidence["predicate"]["abstain"])
        self.assertEqual(v.evidence["extensions_used"], 1)

    def test_unobservable_payload_backstops_with_abstain(self):
        w = FakeWorld(self.run_dir)  # no heartbeat file, no capture answers
        v = _run(w, _policy())
        self.assertEqual(v.action, "kill")
        self.assertEqual(v.kill_reason, "wall_clock_backstop")
        self.assertEqual(v.evidence["predicate"],
                         {"heartbeat_stale": None, "progress_flat": None,
                          "stacks_identical": None,
                          "abstain": "unobservable_payload"})
        self.assertEqual(v.evidence["extensions_used"], 1)

    def test_max_extensions_zero_backstops_immediately(self):
        w = FakeWorld(self.run_dir)
        v = _run(w, _policy(max_extensions=0))
        self.assertEqual((v.action, v.kill_reason),
                         ("kill", "wall_clock_backstop"))
        self.assertEqual(v.evidence["extensions_used"], 0)

    def test_measurement_incomplete_abstains_then_backstops(self):
        # Stale + flat heartbeats, but the capture channel is dead: the two
        # measured conjuncts say "stalled", the missing third abstains.
        w = FakeWorld(self.run_dir)
        w.write_heartbeat({"done": 0}, age_s=100.0)
        v = _run(w, _policy())
        self.assertEqual((v.action, v.kill_reason),
                         ("kill", "wall_clock_backstop"))
        self.assertEqual(v.evidence["predicate"]["heartbeat_stale"], True)
        self.assertEqual(v.evidence["predicate"]["progress_flat"], True)
        self.assertIsNone(v.evidence["predicate"]["stacks_identical"])
        self.assertEqual(v.evidence["predicate"]["abstain"],
                         "measurement_incomplete")

    def test_differing_stacks_read_live(self):
        w = FakeWorld(self.run_dir)
        w.write_heartbeat({"done": 0}, age_s=100.0)  # stale + flat...
        # ...but every capture shows a different frame: the stack is moving.
        w.capture_frames = lambda seq: ["worker.py:%d:loop" % seq]
        v = _run(w, _policy())
        self.assertEqual(v.kill_reason, "wall_clock_backstop")
        self.assertFalse(v.evidence["predicate"]["stacks_identical"])
        self.assertIsNone(v.evidence["predicate"]["abstain"])

    def test_completion_race_never_kills(self):
        w = FakeWorld(self.run_dir)
        w.write_heartbeat({"done": 0}, age_s=100.0)
        real_sleep = w.sleep
        def die_during_interval(d):
            real_sleep(d)
            w.alive = False  # child exits while the ladder waits
        w.sleep = die_during_interval
        v = _run(w, _policy())
        self.assertEqual(v.action, "completed_race")
        self.assertIsNone(v.kill_reason)
        self.assertIsNone(v.evidence["kill_reason"])

    def test_interrupted_aborts_ladder(self):
        w = FakeWorld(self.run_dir)
        w.interrupted = True
        v = _run(w, _policy())
        self.assertEqual(v.action, "interrupted")
        self.assertIsNone(v.kill_reason)

    def test_evidence_is_complete_for_audit(self):
        w = FakeWorld(self.run_dir)
        w.capture_frames = ["f.py:1:g"]
        w.write_heartbeat({"done": 0}, age_s=50.0)
        v = _run(w, _policy())
        ev = v.evidence
        for key in ("schema_version", "kill_reason", "predicate",
                    "heartbeat_age_s", "progress_signatures",
                    "stack_captures", "extensions_used", "policy",
                    "evaluated_at"):
            self.assertIn(key, ev)
        self.assertEqual(ev["policy"]["capture_timeout_s"], 3.0)
        self.assertTrue(ev["progress_signatures"])
        for cap in ev["stack_captures"]:
            self.assertTrue(cap["path"].endswith(
                f"stack_capture_{cap['seq']}.json"))


if __name__ == "__main__":
    unittest.main()
