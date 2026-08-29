"""Raw stdout of the deployed shims against ZCode's HookJSONOutput schema.

This is the only layer with decisions left in it once policy is out of the shims,
and it is where the highest-probability drift lives: the schema below is
transcribed from `resources/glm/zcode.cjs`, ZCode auto-updates, and getting this
wrong is silent — a deny that fails validation THROWS, and a throw does not block.

That already happened: a `{"decision":"deny"}` shape failed validation, surfaced as
`hook.run.failed`, and the tool ran anyway. Twice, live, before it was caught.
"""
import json
import os
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
GUARD = HARNESS / "hooks" / "zproctor_guard.mjs"
NODE = os.environ.get("ZPROCTOR_NODE", r"C:/Program Files/nodejs/node.exe")
HAS_NODE = Path(NODE).is_file()

# Transcribed from resources/glm/zcode.cjs. Keep in sync with the recorded bundle
# hash; on mismatch a parity report is UNVERIFIED, never PASS.
TOP_LEVEL = {"additionalContext", "additional_context", "continue", "decision",
             "hookSpecificOutput", "reason", "stopReason", "suppressOutput"}
DECISION_ENUM = {"approve", "block"}
PRE_TOOL_USE = {"hookEventName", "permissionDecision", "permissionDecisionReason",
                "updatedInput", "additionalContext"}
PERMISSION_ENUM = {"allow", "ask", "deny"}

STORE = "C:/Dev/scratch/zcode-proctor"


@unittest.skipUnless(HAS_NODE, "node not present")
class WireShape(unittest.TestCase):
    def run_guard(self, payload, state=None):
        env = {**os.environ}
        if state:
            env["ZPROCTOR_STATE_ROOT"] = state
        body = payload if isinstance(payload, str) else json.dumps(payload)
        r = subprocess.run([NODE, str(GUARD)], input=body, env=env,
                           capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout.strip()

    def assert_valid(self, raw, event="PreToolUse"):
        """Mirror ZCode's safeParse. Anything it would reject THROWS on its side,
        and a throw does not block — so an invalid shape is a silent allow."""
        obj = json.loads(raw)
        extra = set(obj) - TOP_LEVEL
        self.assertFalse(extra, f"unknown top-level keys: {sorted(extra)}")
        if "decision" in obj:
            self.assertIn(obj["decision"], DECISION_ENUM,
                          "top-level decision accepts only approve|block")
        hso = obj.get("hookSpecificOutput")
        if hso is not None:
            self.assertEqual(hso.get("hookEventName"), event,
                             "hookEventName mismatch throws on ZCode's side")
            extra = set(hso) - PRE_TOOL_USE
            self.assertFalse(extra, f"unknown hookSpecificOutput keys: {sorted(extra)}")
            if hso.get("permissionDecision") is not None:
                self.assertIn(hso["permissionDecision"], PERMISSION_ENUM)
        return obj

    def test_deny_emits_a_shape_zcode_accepts(self):
        rc, out = self.run_guard(
            {"hook_event_name": "PreToolUse", "tool_name": "Write",
             "tool_input": {"file_path": f"{STORE}/events.jsonl"}})
        self.assertEqual(rc, 0, "a non-zero exit that is not 2 throws on ZCode's side")
        obj = self.assert_valid(out)
        self.assertEqual(obj["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_deny_does_not_use_the_flat_decision_field(self):
        """Regression: {"decision":"deny"} is invalid — the enum is approve|block.
        It threw, surfaced as hook.run.failed, and the write went through."""
        _, out = self.run_guard(
            {"hook_event_name": "PreToolUse", "tool_name": "Write",
             "tool_input": {"file_path": f"{STORE}/x"}})
        obj = json.loads(out)
        self.assertNotIn("decision", obj)

    def test_allow_is_silence_not_a_payload(self):
        rc, out = self.run_guard(
            {"hook_event_name": "PreToolUse", "tool_name": "Write",
             "tool_input": {"file_path": "D:/proj/main.py"}})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "", "an allow must emit nothing at all")

    def test_malformed_input_fails_open_silently(self):
        rc, out = self.run_guard("not json at all")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_stdout_is_complete_json_not_truncated(self):
        """process.exit() straight after write() can truncate on a Windows pipe,
        and truncated JSON fails validation, which throws, which allows."""
        _, out = self.run_guard(
            {"hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": f"echo x >> {STORE}/events.jsonl"}})
        self.assertTrue(out.endswith("}"), f"truncated stdout: ...{out[-40:]!r}")
        json.loads(out)

    def test_echoes_the_event_name_it_was_given(self):
        """A hookEventName that does not match the running event throws."""
        _, out = self.run_guard(
            {"hook_event_name": "PreToolUse", "tool_name": "Edit",
             "tool_input": {"file_path": f"{STORE}/events.jsonl"}})
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"],
                         "PreToolUse")


if __name__ == "__main__":
    unittest.main()
