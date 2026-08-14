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


if __name__ == "__main__":
    unittest.main()
