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


if __name__ == "__main__":
    unittest.main()
