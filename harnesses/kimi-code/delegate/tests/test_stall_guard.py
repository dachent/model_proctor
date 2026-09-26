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


if __name__ == "__main__":
    unittest.main()
