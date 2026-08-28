#!/usr/bin/env python3
"""skill_trigger: did the proctor actually get invoked? (#59)

The tool's whole value is in what it refuses to claim, so most of these tests
guard that: no rate is emitted, skips are itemised rather than silent, and an
empty denominator is never papered over. The counting itself is simple.

Synthetic wire logs throughout — no real session data.

Run: python -m unittest discover -s scripts/tests -v
"""

import importlib.util
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "skill_trigger.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("_skill_trigger_under_test",
                                                  TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tool_call(command):
    return {"type": "context.append_loop_event", "time": "2026-01-01T00:00:00",
            "event": {"type": "tool.call", "name": "Bash",
                      "args": {"command": command}}}


class _Base(unittest.TestCase):
    def setUp(self):
        self.m = load_tool()
        self.tmp = tempfile.mkdtemp(prefix="skilltrig-")
        self.root = Path(self.tmp) / "sessions"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_session(self, workdir, session, commands, age_days=0):
        d = self.root / workdir / session / "agents" / "main"
        d.mkdir(parents=True, exist_ok=True)
        wire = d / "wire.jsonl"
        with open(wire, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"type": "task.started",
                                "time": "2026-01-01T00:00:00"}) + "\n")
            for c in commands:
                f.write(json.dumps(tool_call(c)) + "\n")
        if age_days:
            old = time.time() - age_days * 86400
            import os
            os.utime(wire, (old, old))
        return wire


class ClassifyTest(_Base):
    def test_detects_installed_path_invocation(self):
        w = self.make_session("wd_x", "session_a", [
            "python C:/Tools/model-proctor/runner.py init --workspace ws --task t.json"])
        invoked, subs, _cov = self.m.classify(w)
        self.assertTrue(invoked)
        self.assertIn("init", subs)

    def test_detects_repo_relative_invocation(self):
        w = self.make_session("wd_x", "session_b",
                              ["python runner/runner.py verify --workspace ws --task t.json"])
        invoked, subs, _ = self.m.classify(w)
        self.assertTrue(invoked)
        self.assertIn("verify", subs)

    def test_collects_the_full_loop(self):
        w = self.make_session("wd_x", "session_c", [
            "python runner/runner.py lane --task t.json",
            "python runner/runner.py init --workspace ws --task t.json",
            "python runner/runner.py dispatch --workspace ws --task t.json",
            "python runner/runner.py verify --workspace ws --task t.json",
            "python runner/runner.py accept --workspace ws --task t.json",
            "python runner/runner.py record --workspace ws --task t.json",
        ])
        invoked, subs, _ = self.m.classify(w)
        self.assertTrue(invoked)
        for sub in ("lane", "init", "dispatch", "verify", "accept", "record"):
            self.assertIn(sub, subs)

    def test_unrelated_session_is_not_counted(self):
        w = self.make_session("wd_x", "session_d",
                              ["git status", "python -m pytest", "ls -la"])
        invoked, subs, _ = self.m.classify(w)
        self.assertFalse(invoked)
        self.assertEqual(subs, [])

    def test_mention_without_subcommand_still_counts_as_invoked(self):
        """Invoked-but-unclassified must not be silently dropped."""
        w = self.make_session("wd_x", "session_e",
                              ["cat C:/Tools/model-proctor/runner.py"])
        invoked, subs, _ = self.m.classify(w)
        self.assertTrue(invoked, "a runner mention counts as invoked")
        self.assertEqual(subs, [], "with no subcommand classified")


class ScanTest(_Base):
    def test_counts_and_workdir_breakdown(self):
        self.make_session("wd_a", "s1", ["python runner/runner.py init -x"])
        self.make_session("wd_a", "s2", ["git status"])
        self.make_session("wd_b", "s3", ["git log"])
        sessions, skipped, _drift = self.m.scan(self.root, 0, 0)
        self.assertEqual(len(sessions), 3)
        self.assertEqual(sum(1 for s in sessions if s["invoked"]), 1)

    def test_missing_root_is_not_a_crash(self):
        sessions, skipped, _ = self.m.scan(Path(self.tmp) / "nope", 0, 0)
        self.assertEqual(sessions, [])


class SkipAccountingTest(_Base):
    """No silent caps — the coverage-manifest discipline extract_log sets."""

    def test_age_window_skips_are_counted(self):
        self.make_session("wd_a", "s1", ["git status"], age_days=90)
        self.make_session("wd_a", "s2", ["git status"])
        _wires, skipped = self.m.find_wires(self.root, 30, 0)
        self.assertEqual(skipped["too_old"], 1)

    def test_size_cap_skips_are_counted_and_sized(self):
        w = self.make_session("wd_a", "s1", ["git status"])
        with open(w, "a", encoding="utf-8") as f:
            f.write(" " * 5000)
        _wires, skipped = self.m.find_wires(self.root, 0, 1000)
        self.assertEqual(skipped["too_large"], 1)
        self.assertGreater(
            skipped["largest_skipped_bytes"], 1000,
            "what was skipped must be reportable at any scale, not rounded "
            "away — a MB figure reads 0.0 for everything under a megabyte")

    def test_zero_disables_the_caps(self):
        self.make_session("wd_a", "s1", ["git status"], age_days=900)
        wires, skipped = self.m.find_wires(self.root, 0, 0)
        self.assertEqual(len(wires), 1)
        self.assertEqual(skipped["too_old"], 0)


class NoRateTest(_Base):
    """The refusal that makes this honest.

    The tool counts sessions that DID invoke the runner. It cannot judge which
    SHOULD have — that is the discretionary call the skill description exists
    to make. So there is no denominator and must be no rate.
    """

    def _result(self):
        import contextlib
        import io
        self.make_session("wd_a", "s1", ["python runner/runner.py init -x"])
        self.make_session("wd_a", "s2", ["git status"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.m.main(["--sessions-root", str(self.root), "--since-days", "0",
                         "--max-bytes", "0", "--json"])
        return json.loads(buf.getvalue())

    def test_rate_is_explicitly_null_with_a_reason(self):
        r = self._result()
        self.assertIsNone(r["rate"])
        self.assertIn("no denominator", r["rate_note"])

    def test_no_percentage_key_sneaks_in(self):
        r = self._result()
        for k in r:
            self.assertNotIn("percent", k.lower())
            self.assertNotIn("ratio", k.lower())

    def test_numerator_fields_are_present_and_named_as_counts(self):
        r = self._result()
        self.assertEqual(r["sessions_scanned"], 2)
        self.assertEqual(r["sessions_invoking_runner"], 1)
        self.assertEqual(r["sessions_reaching_init"], 1)

    def test_text_output_says_no_rate(self):
        import contextlib
        import io
        self.make_session("wd_a", "s1", ["git status"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.m.main(["--sessions-root", str(self.root), "--since-days", "0",
                         "--max-bytes", "0"])
        self.assertIn("NO RATE IS REPORTED", buf.getvalue())


class DriftPassthroughTest(_Base):
    def test_unrecognized_event_types_are_surfaced(self):
        """The scan sees every session, so it is a broader drift sample than
        any single capture — that is how #53's first refresh was found to be
        incomplete."""
        d = self.root / "wd_a" / "s1" / "agents" / "main"
        d.mkdir(parents=True)
        with open(d / "wire.jsonl", "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"type": "task.started"}) + "\n")
            f.write(json.dumps({"type": "totally.new.event"}) + "\n")
        _sessions, _skipped, drift = self.m.scan(self.root, 0, 0)
        self.assertIn("totally.new.event", drift)


if __name__ == "__main__":
    unittest.main()
