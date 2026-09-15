"""Offline operator-flow proof through the real Codex delegate boundary."""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


DELEGATE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DELEGATE_DIR))

import delegate  # noqa: E402


CATALOG = {"data": [
    {"id": "gpt-5.6-luna", "defaultReasoningEffort": "medium",
     "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]},
    {"id": "gpt-5.6-terra", "defaultReasoningEffort": "medium",
     "supportedReasoningEfforts": [{"reasoningEffort": "medium"}, {"reasoningEffort": "high"}]},
    {"id": "gpt-6-astra", "defaultReasoningEffort": "low",
     "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "max"}]},
]}


class FakeStdin(io.BytesIO):
    def close(self):
        pass


class FakeProcess:
    def __init__(self, events):
        self.stdin = FakeStdin()
        self.stdout = io.BytesIO(b"".join(
            json.dumps(event).encode("utf-8") + b"\n" for event in events
        ))

    def wait(self, timeout=None):
        return 0

    def kill(self):
        return None


class OperatorFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="codex-operator-flow-"))
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.config = self.tmp / "local-config.json"
        if not (DELEGATE_DIR / "local-config.example.json").is_file():
            self.fail("tracked local-config.example.json is absent")
        self.config.write_bytes((DELEGATE_DIR / "local-config.example.json").read_bytes())
        self.task = self.tmp / "operator-task.txt"
        self.task.write_text("Reply exactly MODEL_PROCTOR_SMOKE_OK.", encoding="utf-8")
        self.calls = []
        self.processes = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_named_preset_uses_only_the_requested_cli_transport(self):
        """A Terra preset must not drift model/effort or fall back to app-server."""
        def popen(argv, **kwargs):
            self.calls.append((argv, kwargs))
            events = ([{"id": 1, "result": {}}, {"id": 2, "result": CATALOG}]
                      if len(self.calls) == 1 else [
                {"type": "thread.started", "thread_id": "session-offline"},
                {"type": "turn.completed", "turn_id": "turn-offline"},
            ])
            proc = FakeProcess(events)
            self.processes.append(proc)
            return proc

        args = argparse.Namespace(
            model=None, preset="terra", effort=None, transport="cli", write=False,
            task_file=str(self.task), workspace=str(self.workspace), config=str(self.config),
            codex_executable="fake-codex", resume_identity=None,
        )
        result, code = delegate.run_delegate(
            args, catalog_payload=None, popen_factory=popen, environ={},
        )

        self.assertEqual(code, delegate.EXIT_OK)
        self.assertEqual(
            {key: result[key] for key in ("model", "effort", "transport", "sandbox", "status")},
            {"model": "gpt-5.6-terra", "effort": "high", "transport": "cli",
             "sandbox": "read-only", "status": "completed"},
        )
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][0], ["fake-codex", "app-server", "--stdio"])
        self.assertEqual(self.calls[1][0], ["fake-codex", "exec", "-m", "gpt-5.6-terra", "-c",
            "model_reasoning_effort=high", "-s", "read-only", "-C",
            str(self.workspace.resolve()), "--json", "-"])
        self.assertEqual(self.calls[1][1]["cwd"], str(self.workspace.resolve()))
        self.assertEqual(self.calls[1][1]["stdin"], delegate.subprocess.PIPE)
        self.assertEqual(self.calls[1][1]["stdout"], delegate.subprocess.PIPE)
        self.assertEqual(self.calls[1][1]["env"]["PROCTOR_CHILD"], "1")
        self.assertEqual(self.processes[1].stdin.getvalue(), b"Reply exactly MODEL_PROCTOR_SMOKE_OK.")
        preflight = [json.loads(line) for line in self.processes[0].stdin.getvalue().splitlines()]
        self.assertEqual([request["method"] for request in preflight],
                         ["initialize", "initialized", "model/list"])
        self.assertFalse(any(request["method"].startswith(("thread/", "turn/")) for request in preflight))


if __name__ == "__main__":
    unittest.main()
