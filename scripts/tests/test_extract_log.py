#!/usr/bin/env python3
"""Tests for scripts/extract_log.py — synthetic wire.jsonl fixtures.

Covers: full-parse case (tool calls, file mutations, gh/git commands,
assistant text lengths, usage records), unknown-event-type coverage
accounting, malformed-line accounting, and byte-level determinism.

Run: python -m unittest discover -s scripts/tests -v
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
EXTRACTOR = SCRIPTS_DIR / "extract_log.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import extract_log  # noqa: E402


def _loop(inner, t=1000):
    return {"type": "context.append_loop_event", "event": inner, "time": t}


FULL_WIRE = [
    {"type": "metadata", "protocol_version": "1.4", "created_at": 999},
    {"type": "turn.prompt",
     "input": [{"type": "text", "text": "do the task"}], "time": 1000},
    _loop({"type": "step.begin", "turnId": "0", "step": 1}, 1001),
    _loop({"type": "content.part", "turnId": "0", "step": 1,
           "part": {"type": "think", "think": "hmm"}}, 1002),
    _loop({"type": "content.part", "turnId": "0", "step": 1,
           "part": {"type": "text", "text": "I will edit the file."}}, 1003),
    _loop({"type": "tool.call", "turnId": "0", "step": 1, "name": "Read",
           "args": {"path": "src/a.py"}}, 1004),
    _loop({"type": "tool.result", "toolCallId": "Read_0",
           "result": {"output": "..."}}, 1005),
    _loop({"type": "tool.call", "turnId": "0", "step": 2, "name": "Edit",
           "args": {"path": "src/a.py", "old_string": "x", "new_string": "y"}}, 1006),
    _loop({"type": "tool.call", "turnId": "0", "step": 3, "name": "Write",
           "args": {"path": "src/b.py", "content": "..."}}, 1007),
    _loop({"type": "tool.call", "turnId": "0", "step": 4, "name": "Bash",
           "args": {"command": "git status --porcelain && git diff"}}, 1008),
    _loop({"type": "tool.call", "turnId": "0", "step": 5, "name": "Bash",
           "args": {"command": "gh pr view 12"}}, 1009),
    _loop({"type": "tool.call", "turnId": "0", "step": 6, "name": "Bash",
           "args": {"command": "python -m unittest discover -s tests"}}, 1010),
    _loop({"type": "step.end", "turnId": "0", "step": 6,
           "usage": {"inputOther": 10}}, 1011),
    {"type": "usage.record", "model": "fireworks/kimi-k3",
     "usage": {"inputOther": 10, "output": 5,
               "inputCacheRead": 0, "inputCacheCreation": 0}, "time": 1012},
]


class ExtractLogTestCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="extract-log-test-"))
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_wire(self, name, lines):
        """lines: list of dicts (serialized) or raw strings (written verbatim)."""
        path = self.tmp / name
        with open(path, "w", encoding="utf-8") as f:
            for item in lines:
                if isinstance(item, str):
                    f.write(item + "\n")
                else:
                    f.write(json.dumps(item) + "\n")
        return path

    def run_cli(self, *wire_files, out=None):
        out = out or (self.tmp / "out")
        result = subprocess.run(
            [sys.executable, str(EXTRACTOR),
             *[str(w) for w in wire_files], "--out", str(out)],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        return out, manifest

    # -- full parse ---------------------------------------------------------

    def test_full_parse(self):
        wire = self.write_wire("wire.jsonl", FULL_WIRE)
        facts, coverage = extract_log.extract_file(wire)

        self.assertEqual(coverage["records_unrecognized"], 0)
        self.assertEqual(coverage["malformed_lines"], 0)
        self.assertEqual(coverage["records_parsed"], len(FULL_WIRE))
        self.assertEqual(coverage["lines_total"], len(FULL_WIRE))
        self.assertEqual(coverage["bytes_in"], wire.stat().st_size)
        self.assertFalse(coverage["truncated"])
        self.assertFalse(coverage["skipped"])

        self.assertEqual(len(facts["tool_calls"]), 6)
        self.assertEqual(facts["tool_calls"][0]["name"], "Read")
        self.assertEqual(facts["tool_calls"][0]["summary"], "src/a.py")
        self.assertEqual(facts["tool_calls"][0]["time"], 1004)

        self.assertEqual(
            [(m["kind"], m["path"]) for m in facts["file_mutations"]],
            [("edit", "src/a.py"), ("write", "src/b.py")])

        commands = [g["command"] for g in facts["gh_git_commands"]]
        self.assertEqual(commands, ["git status --porcelain && git diff",
                                    "gh pr view 12"])

        self.assertEqual(facts["assistant_text_lengths"],
                         [{"time": 1003, "turn": "0",
                           "chars": len("I will edit the file.")}])
        self.assertEqual(facts["usage_records"], 1)

    # -- coverage: unknown event types --------------------------------------

    def test_unknown_event_types_counted_in_coverage(self):
        wire = self.write_wire("wire.jsonl", [
            {"type": "metadata", "protocol_version": "1.4"},
            {"type": "future.new_event", "payload": 1, "time": 5},
            {"type": "future.new_event", "payload": 2, "time": 6},
            {"type": "another_unknown"},
            _loop({"type": "loop.unknown_inner", "turnId": "0"}, 1000),
        ])
        facts, coverage = extract_log.extract_file(wire)
        self.assertEqual(coverage["records_parsed"], 2)  # metadata + loop wrapper
        self.assertEqual(coverage["records_unrecognized"], 4)
        self.assertEqual(coverage["unrecognized_types"],
                         {"future.new_event": 2, "another_unknown": 1,
                          "loop:loop.unknown_inner": 1})
        self.assertEqual(facts["tool_calls"], [])

    # -- coverage: malformed lines ------------------------------------------

    def test_malformed_lines_counted(self):
        wire = self.write_wire("wire.jsonl", [
            {"type": "metadata"},
            "{not json",
            "",
            "[1, 2, 3]",  # valid JSON but not an object
            _loop({"type": "step.begin", "turnId": "0", "step": 1}),
        ])
        facts, coverage = extract_log.extract_file(wire)
        self.assertEqual(coverage["malformed_lines"], 2)
        self.assertEqual(coverage["blank_lines_skipped"], 1)
        self.assertEqual(coverage["records_parsed"], 2)
        self.assertEqual(coverage["lines_total"], 5)

    # -- CLI + determinism ----------------------------------------------------

    def test_cli_writes_facts_and_manifest(self):
        wire = self.write_wire("wire.jsonl", FULL_WIRE)
        out, manifest = self.run_cli(wire)
        entry = manifest["files"][0]
        self.assertEqual(entry["coverage"]["records_unrecognized"], 0)
        facts_path = out / entry["facts_file"]
        self.assertTrue(facts_path.is_file())
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        self.assertEqual(len(facts["tool_calls"]), 6)

    def test_deterministic_byte_identical_output(self):
        wire = self.write_wire("wire.jsonl", FULL_WIRE)
        out1 = self.tmp / "out1"
        out2 = self.tmp / "out2"
        self.run_cli(wire, out=out1)
        self.run_cli(wire, out=out2)
        for name in ("manifest.json",):
            self.assertEqual((out1 / name).read_bytes(),
                             (out2 / name).read_bytes())
        f1 = sorted(p for p in out1.iterdir() if p.name != "manifest.json")
        f2 = sorted(p for p in out2.iterdir() if p.name != "manifest.json")
        self.assertEqual([p.name for p in f1], [p.name for p in f2])
        for a, b in zip(f1, f2):
            self.assertEqual(a.read_bytes(), b.read_bytes())


class ExtractLogExtensionTestCase(ExtractLogTestCase):
    """The 2026-10-29 fields: results, digests, owner text, turns, tasks,
    aliases — the facts the escalation triggers are allowed to read."""

    EXTENDED_WIRE = [
        {"type": "metadata", "protocol_version": "1.4", "time": 1000},
        {"type": "agent.turn.started", "turnId": 0, "time": 1100},
        {"type": "turn.prompt", "input": [{"type": "text", "text": "Fix it."}],
         "time": 1101},
        {"type": "turn.prompt",
         "input": [{"type": "text",
                    "text": '<notification id="task:x"><Title>done'}],
         "time": 1102},
        {"type": "turn.steer",
         "origin": {"kind": "user"},
         "input": [{"type": "text", "text": "Why did you stop?"}],
         "time": 1103},
        {"type": "turn.steer",
         "origin": {"kind": "agent"},
         "input": [{"type": "text", "text": "agent chatter"}],
         "time": 1104},
        {"type": "task.started",
         "info": {"taskId": "bash-1", "status": "running", "detached": True,
                  "startedAt": 1200, "endedAt": None, "exitCode": None,
                  "description": "poll"},
         "time": 1200},
        {"type": "task.terminated",
         "info": {"taskId": "bash-1", "status": "completed",
                  "startedAt": 1200, "endedAt": 1500, "exitCode": 0},
         "time": 1500},
        {"type": "llm.request", "model": "accounts/fireworks/x/flash",
         "modelAlias": "fireworks/flash", "time": 1300},
        _loop({"type": "content.part", "turnId": "0", "step": 1,
               "part": {"type": "text", "text": "I will fix the test."}}, 1301),
        _loop({"type": "tool.call", "turnId": "0", "step": 1, "name": "Bash",
               "toolCallId": "c1",
               "args": {"command": "pwsh -File bb.ps1 stop 1431"}}, 1302),
        _loop({"type": "tool.result", "toolCallId": "c1",
               "result": {"output": "bb: HTTP 404 on POST\n"
                                     "Command failed with exit code: 1",
                          "durationMs": 12}}, 1303),
        _loop({"type": "tool.call", "turnId": "0", "step": 2, "name": "Bash",
               "toolCallId": "c2", "args": {"command": "sleep 5"}}, 1304),
        _loop({"type": "tool.result", "toolCallId": "c2",
               "result": {"output": "ok", "durationMs": 5000}}, 1305),
        {"type": "turn.ended", "turnId": 0, "durationMs": 900000,
         "reason": "completed", "time": 1400},
        {"type": "subagent.spawned", "time": 1401},
        {"type": "file_history.tracked", "time": 1402},
        {"type": "token_counting.rebased", "time": 1403},
    ]

    def test_tool_results_carry_exit_code_error_flag_and_tail(self):
        wire = self.write_wire("wire.jsonl", self.EXTENDED_WIRE)
        facts, _ = extract_log.extract_file(wire)
        self.assertEqual(len(facts["tool_results"]), 2)
        failed, ok = facts["tool_results"]
        self.assertEqual(failed["name"], "Bash")
        self.assertEqual(failed["command"], "pwsh -File bb.ps1 stop 1431")
        self.assertEqual(failed["exit_code"], 1)
        self.assertTrue(failed["is_error"] is False)  # wire says isError absent
        self.assertIn("HTTP 404", failed["tail"])
        self.assertIsNone(ok["exit_code"])
        self.assertIn("ok", ok["tail"])

    def test_tool_call_carries_command_and_call_id(self):
        wire = self.write_wire("wire.jsonl", self.EXTENDED_WIRE)
        facts, _ = extract_log.extract_file(wire)
        self.assertEqual(facts["tool_calls"][0]["tool_call_id"], "c1")
        self.assertEqual(facts["tool_calls"][0]["command"],
                         "pwsh -File bb.ps1 stop 1431")

    def test_text_digests_normalize_case_and_punctuation(self):
        self.assertEqual(extract_log._text_digest("Why did you stop?"),
                         extract_log._text_digest("why did you stop"))
        self.assertNotEqual(extract_log._text_digest("fix ticket 675"),
                            extract_log._text_digest("fix ticket 676"))

    def test_owner_texts_flag_system_envelopes_and_drop_agent_steers(self):
        wire = self.write_wire("wire.jsonl", self.EXTENDED_WIRE)
        facts, _ = extract_log.extract_file(wire)
        owners = [(o["source"], o["system"], o["excerpt"])
                  for o in facts["owner_texts"]]
        self.assertEqual(owners, [
            ("turn.prompt", False, "Fix it."),
            ("turn.prompt", True, '<notification id="task:x"><Title>done'),
            ("turn.steer", False, "Why did you stop?"),
        ])

    def test_turns_and_tasks_join_both_halves(self):
        wire = self.write_wire("wire.jsonl", self.EXTENDED_WIRE)
        facts, _ = extract_log.extract_file(wire)
        self.assertEqual(facts["turns"], [{
            "turn": "0", "started_at": 1100, "ended_at": 1400,
            "duration_ms": 900000, "reason": "completed"}])
        self.assertEqual(facts["tasks"], [{
            "task_id": "bash-1", "started_at": 1200, "ended_at": 1500,
            "status": "completed", "exit_code": 0, "detached": True,
            "description": "poll"}])

    def test_model_alias_extracted(self):
        wire = self.write_wire("wire.jsonl", self.EXTENDED_WIRE)
        facts, _ = extract_log.extract_file(wire)
        self.assertEqual(facts["model_aliases"], [{
            "time": 1300, "model": "accounts/fireworks/x/flash",
            "alias": "fireworks/flash"}])

    def test_refreshed_top_level_types_are_recognized(self):
        wire = self.write_wire("wire.jsonl", self.EXTENDED_WIRE)
        _, coverage = extract_log.extract_file(wire)
        self.assertEqual(coverage["records_unrecognized"], 0,
                         coverage["unrecognized_types"])


if __name__ == "__main__":
    unittest.main()
