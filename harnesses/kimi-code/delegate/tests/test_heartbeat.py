#!/usr/bin/env python3
"""Payload heartbeat protocol (#105/TOOL-034): emitter, rotation, bounded reader.

Run: python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import heartbeat  # noqa: E402


class HeartbeatTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hb-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = str(Path(self.tmp) / "hb.jsonl")

    def _record(self, **over):
        rec = {"v": 1, "ts": "2026-09-25T10:00:00", "epoch": 1000.0,
               "pid": 1, "seq": 1, "dispatch_id": "d-1",
               "stage": "implement", "sub_stage": None, "counters": {}}
        rec.update(over)
        return rec


class EmitTest(HeartbeatTestBase):
    def test_emit_writes_one_schema_record(self):
        rec = heartbeat.emit("implement", sub_stage="tests",
                             counters={"tests_run": 3},
                             path=self.path, env={"DELEGATE_DISPATCH_ID": "d-1"})
        self.assertEqual(rec["v"], heartbeat.SCHEMA_VERSION)
        self.assertEqual(rec["stage"], "implement")
        self.assertEqual(rec["sub_stage"], "tests")
        self.assertEqual(rec["counters"], {"tests_run": 3})
        self.assertEqual(rec["dispatch_id"], "d-1")
        self.assertEqual(rec["pid"], os.getpid())
        self.assertIsInstance(rec["seq"], int)
        self.assertIsInstance(rec["epoch"], float)
        lines = Path(self.path).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["stage"], "implement")

    def test_seq_is_monotonic_within_process(self):
        a = heartbeat.emit("a", path=self.path)
        b = heartbeat.emit("b", path=self.path)
        self.assertGreater(b["seq"], a["seq"])

    def test_emit_uses_env_path_and_returns_none_without_one(self):
        rec = heartbeat.emit("intake", env={"DELEGATE_HEARTBEAT_PATH": self.path})
        self.assertIsNotNone(rec)
        self.assertTrue(Path(self.path).is_file())
        self.assertIsNone(heartbeat.emit("intake", env={}))

    def test_emit_never_raises_on_io_failure(self):
        blocker = Path(self.tmp) / "blocker"
        blocker.write_text("x", encoding="utf-8")
        # path's parent component is a FILE: makedirs/open must fail.
        self.assertIsNone(heartbeat.emit("s", path=str(blocker / "hb.jsonl")))

    def test_rotation_keeps_file_bounded_and_parseable(self):
        pad = "x" * 1000
        for i in range(200):  # ~210 KB of appends against a 64 KiB cap
            heartbeat.emit(f"stage-{i}", counters={"pad": pad}, path=self.path)
        size = os.path.getsize(self.path)
        self.assertLessEqual(size, heartbeat.MAX_HEARTBEAT_BYTES + 2048)
        rec, status = heartbeat.read_latest(self.path)
        self.assertEqual(status, "ok")
        self.assertEqual(rec["stage"], "stage-199")


class ReadLatestTest(HeartbeatTestBase):
    def test_absent(self):
        rec, status = heartbeat.read_latest(str(Path(self.tmp) / "nope.jsonl"))
        self.assertEqual((rec, status), (None, "absent"))

    def test_empty(self):
        Path(self.path).write_text("", encoding="utf-8")
        self.assertEqual(heartbeat.read_latest(self.path), (None, "empty"))

    def test_torn_tail_is_skipped(self):
        good = self._record(stage="implement")
        Path(self.path).write_text(json.dumps(good) + "\n" + '{"v": 1, "sta',
                                   encoding="utf-8")
        rec, status = heartbeat.read_latest(self.path)
        self.assertEqual(status, "ok")
        self.assertEqual(rec["stage"], "implement")

    def test_all_garbage_is_corrupt(self):
        Path(self.path).write_text('garbage\n{"v":1\n', encoding="utf-8")
        self.assertEqual(heartbeat.read_latest(self.path), (None, "corrupt"))

    def test_unreadable(self):
        # NOTE: a FILE as a parent path component raises FileNotFoundError
        # on Windows (verified 3.10/3.14: ERROR_PATH_NOT_FOUND maps to FNF),
        # which correctly reads as "absent" — so the unreadable branch is
        # forced with a patched getsize raising a non-FNF OSError.
        Path(self.path).write_text("{}\n", encoding="utf-8")
        with mock.patch.object(
                heartbeat.os.path, "getsize",
                side_effect=PermissionError("denied")):
            rec, status = heartbeat.read_latest(self.path)
        self.assertEqual((rec, status), (None, "unreadable"))

    def test_read_is_bounded_to_tail(self):
        good = self._record(stage="verify")
        pad = "g" * (heartbeat.READ_TAIL_BYTES * 2)
        Path(self.path).write_text(pad + "\n" + json.dumps(good) + "\n",
                                   encoding="utf-8")
        rec, status = heartbeat.read_latest(self.path)
        self.assertEqual(status, "ok")
        self.assertEqual(rec["stage"], "verify")

    def test_is_stale(self):
        now = time.time()
        self.assertTrue(heartbeat.is_stale({"epoch": now - 3600}, now=now))
        self.assertFalse(heartbeat.is_stale({"epoch": now}, now=now))
        # Unjudgeable freshness is None, not True: callers degrade, not stale.
        self.assertIsNone(heartbeat.is_stale({"epoch": "not-a-number"}, now=now))
        self.assertIsNone(heartbeat.is_stale(None))


from test_delegate import DelegateTestBase, make_agent  # noqa: E402

_ENV_DUMP = (
    "import os, pathlib\n"
    "pathlib.Path('env_dump.txt').write_text(\n"
    "    (os.environ.get('DELEGATE_HEARTBEAT_PATH') or '<absent>') + '\\n' +\n"
    "    (os.environ.get('DELEGATE_DISPATCH_ID') or '<absent>'),\n"
    "    encoding='utf-8')\n"
)


class TestHeartbeatEnvInjection(DelegateTestBase):
    """#105: the delegate injects the heartbeat side-channel coordinates
    into the child env when (and only when) the runner passes the flags."""

    def setUp(self):
        super().setUp()
        dump_script = self._script("env_dump", _ENV_DUMP)
        self.config_path = self._config({"test-agent": make_agent(dump_script)})

    def test_heartbeat_env_injected_when_flags_passed(self):
        hb = str(Path(self.tmpdir) / "hb.jsonl")
        out, err, rc = self._run("test-agent", task="hello",
                                 extra_argv=["--heartbeat-file", hb,
                                             "--dispatch-id", "d-42"])
        self._assert_result(out, err, rc, "completed", 0)
        dumped = (Path(self.workspace) / "env_dump.txt").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(dumped, [hb, "d-42"])

    def test_no_flags_no_env(self):
        out, err, rc = self._run("test-agent", task="hello")
        self._assert_result(out, err, rc, "completed", 0)
        dumped = (Path(self.workspace) / "env_dump.txt").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(dumped, ["<absent>", "<absent>"])


if __name__ == "__main__":
    unittest.main()
