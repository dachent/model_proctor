"""ZCode harness: acceptance gate, sealing, scope, dispatch gate.

Ported from the implementation work; every case here corresponds to an invariant
in policy/HARNESS_CONTRACT.md. The pass-then-fail journal case exists because its
absence let the control plane and its own gate drift into a deadlock while a
51-test suite stayed green.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HARNESS = Path(__file__).resolve().parents[1]
ZPROCTOR = HARNESS / "zproctor.py"
GATE = HARNESS / "hooks" / "zproctor_gate.mjs"
GUARD = HARNESS / "hooks" / "zproctor_guard.mjs"
NODE = os.environ.get("ZPROCTOR_NODE", r"C:/Program Files/nodejs/node.exe")

HAS_NODE = Path(NODE).is_file()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zproctor-"))
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        self.state = self.tmp / "state"
        self.env = {**os.environ, "ZPROCTOR_STATE_ROOT": str(self.state)}
        (self.ws / "flag.txt").write_text("bad\n", encoding="utf-8")
        (self.ws / "check.py").write_text(
            "v = open('flag.txt').read().strip()\n"
            "assert v == 'good', f'flag is {v}'\n"
            "print('OK')\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def zp(self, *args):
        r = subprocess.run([sys.executable, str(ZPROCTOR), *args],
                           env=self.env, capture_output=True, text=True)
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            self.fail(f"non-JSON from zproctor {args}: {r.stdout[:200]} {r.stderr[-200:]}")

    def lane(self, task="t", *flags):
        return self.zp("lane", "--task", task, "--workspace", str(self.ws), *flags)

    def init(self, task="t", *extra):
        return self.zp("init", "--task", task, "--workspace", str(self.ws),
                       *extra, "--verifier", "python", "check.py")

    def verify(self, task="t"):
        return self.zp("verify", "--task", task, "--workspace", str(self.ws))

    def accept(self, task="t"):
        return self.zp("accept", "--task", task, "--workspace", str(self.ws))

    def gate(self, agent):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                   "cwd": str(self.ws), "tool_input": {"subagent_type": agent}}
        r = subprocess.run([NODE, str(GATE)], input=json.dumps(payload),
                           env=self.env, capture_output=True, text=True, timeout=30)
        if not r.stdout.strip():
            return None
        return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


class AcceptanceGate(Base):
    def test_accept_refuses_on_failed_verify(self):
        self.lane("t", "--bounded")
        self.init()
        self.assertFalse(self.verify()["passed"])
        self.assertFalse(self.accept()["ok"])

    def test_accept_succeeds_then_stales_on_mutation(self):
        self.lane("t", "--bounded")
        self.init()
        (self.ws / "flag.txt").write_text("good\n", encoding="utf-8")
        self.assertTrue(self.verify()["passed"])
        self.assertTrue(self.accept()["ok"])
        (self.ws / "flag.txt").write_text("good\n# touched\n", encoding="utf-8")
        out = self.accept()
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "receipt_stale")

    def test_pass_clears_the_stagnation_run(self):
        """FAIL x3, PASS, FAIL must read as run=1, not run=4.

        Without this the control plane says lateral_switch while the dispatch gate,
        which does clear on pass, refuses the escalation. Deadlock, observed live.
        """
        self.lane("t", "--bounded")
        self.init()
        for _ in range(3):
            self.verify()
        (self.ws / "flag.txt").write_text("good\n", encoding="utf-8")
        self.assertTrue(self.verify()["passed"])
        (self.ws / "flag.txt").write_text("bad\n", encoding="utf-8")
        out = self.verify()
        self.assertEqual(out["stagnant_run"], 1)
        self.assertEqual(out["next"], "same_worker_repair")


class Sealing(Base):
    def test_verifier_is_sealed_and_restored(self):
        self.lane("t", "--bounded")
        out = self.init()
        self.assertIn("check.py", out["sealed"])
        (self.ws / "check.py").write_text("print('OK')\n", encoding="utf-8")  # trivially passes
        res = self.verify()
        restored = [r["path"] for r in res["verifier_tampered"] if r["restored"]]
        self.assertIn("check.py", restored)
        self.assertFalse(res["passed"], "the restored verifier must still fail")
        self.assertIn("assert v == 'good'",
                      (self.ws / "check.py").read_text(encoding="utf-8"))

    def test_new_verification_affecting_file_refuses(self):
        self.lane("t", "--bounded")
        self.init()
        (self.ws / "conftest.py").write_text("# sneaky\n", encoding="utf-8")
        out = self.verify()
        self.assertEqual(out["error"], "verification_surface_changed")


class Scope(Base):
    def _split(self):
        (self.ws / "src").mkdir()
        (self.ws / "src" / "deep").mkdir()
        (self.ws / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.ws / "src" / "deep" / "b.py").write_text("y = 1\n", encoding="utf-8")
        (self.ws / "outside.py").write_text("# out\n", encoding="utf-8")

    def test_deep_glob_covers_nested(self):
        self._split()
        self.lane("t", "--bounded")
        # flag.txt is in scope too: the repair below flips it, and a change outside
        # the declared scope is a violation regardless of which file it is.
        self.init("t", "--scope", "src/**", "flag.txt")
        (self.ws / "src" / "deep" / "b.py").write_text("y = 2\n", encoding="utf-8")
        (self.ws / "flag.txt").write_text("good\n", encoding="utf-8")
        self.assertTrue(self.verify()["passed"])

    def test_single_level_glob_does_not_cover_nested(self):
        self._split()
        self.lane("t", "--bounded")
        self.init("t", "--scope", "src/*")
        (self.ws / "src" / "deep" / "b.py").write_text("y = 2\n", encoding="utf-8")
        out = self.verify()
        self.assertEqual(out["error"], "scope_violation")
        self.assertIn("src/deep/b.py", out["out_of_scope"])

    def test_out_of_scope_change_refused(self):
        self._split()
        self.lane("t", "--bounded")
        self.init("t", "--scope", "src/**")
        (self.ws / "outside.py").write_text("# CHANGED\n", encoding="utf-8")
        self.assertEqual(self.verify()["error"], "scope_violation")


class LaneFreeze(Base):
    def test_lane_frozen_for_the_task(self):
        self.assertEqual(self.lane("t", "--bounded")["lane"], "substantial")
        out = self.lane("t", "--bounded", "--known-location", "--objective-acceptance")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "lane_already_selected")

    def test_new_task_id_cannot_buy_a_fresh_lane(self):
        """Per-task freeze alone is not enough: a second task id in the same
        workspace would re-lane with friendlier features. Observed live."""
        self.lane("one", "--bounded")
        self.init("one")
        out = self.lane("two", "--marathon")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "workspace_has_live_task")

    def test_relane_requires_a_recorded_reason(self):
        self.lane("t", "--bounded")
        self.assertEqual(self.lane("t", "--marathon", "--relane")["error"],
                         "relane_reason_required")
        out = self.lane("t", "--marathon", "--relane", "--reason", "owner override")
        self.assertTrue(out["ok"])
        self.assertEqual(out["relaned_from"]["lane"], "substantial")


@unittest.skipUnless(HAS_NODE, "node not present")
class DispatchGate(Base):
    def test_no_lane_refuses_dispatch(self):
        self.assertIn("no lane has been selected", self.gate("escalate-glm"))

    def test_cheap_lane_binds_to_self_and_refuses_dispatch(self):
        self.lane("t", "--bounded", "--known-location", "--objective-acceptance")
        msg = self.gate("escalate-glm")
        self.assertIn('lane "cheap"', msg)
        self.assertIn('binds to "self"', msg)

    def test_assigned_worker_allowed(self):
        self.lane("t", "--bounded")
        self.assertIsNone(self.gate("escalate-glm"))

    def test_next_tier_needs_recorded_stagnation(self):
        self.lane("t", "--bounded")
        self.init()
        self.assertIn("requires recorded stagnation", self.gate("escalate-k3"))
        for _ in range(3):
            self.verify()
        self.assertIsNone(self.gate("escalate-k3"))

    def test_ungated_agents_pass_through(self):
        self.lane("t", "--bounded")
        for agent in ("general-purpose", "Explore"):
            self.assertIsNone(self.gate(agent), f"{agent} must not be gated")

    def test_shim_holds_no_policy(self):
        """The constants must live in lane.json, not in the shim."""
        src = GATE.read_text(encoding="utf-8")
        for banned in ("?? 3", "?? 6", "js.run < 3", "const TIERS"):
            self.assertNotIn(banned, src,
                             f"policy constant {banned!r} leaked back into the shim")


if __name__ == "__main__":
    unittest.main()
