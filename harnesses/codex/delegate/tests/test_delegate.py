"""Deterministic contract tests for the bounded Codex transport adapter."""
import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

import delegate  # noqa: E402


CATALOG = {"data": [{"id": "gpt-test", "defaultReasoningEffort": "medium",
                      "supportedReasoningEfforts": [
                          {"reasoningEffort": "low"}, {"reasoningEffort": "medium"}]}]}
TOKEN_USAGE = {"last": {"cachedInputTokens": 1, "inputTokens": 2, "outputTokens": 3,
                        "reasoningOutputTokens": 4, "totalTokens": 10},
               "total": {"cachedInputTokens": 5, "inputTokens": 6, "outputTokens": 7,
                         "reasoningOutputTokens": 8, "totalTokens": 26},
               "modelContextWindow": 128000}


class FakeStdin(io.BytesIO):
    def close(self): pass


class FakeProcess:
    def __init__(self, lines):
        self.stdin = FakeStdin()
        self.stdout = io.BytesIO("".join(json.dumps(x) + "\n" for x in lines).encode())
        self.stderr = io.BytesIO()
        self.returncode = 0
        self.wait_calls = 0
    def wait(self, *args, **kwargs):
        self.wait_calls += 1
        return 0


class UnreapableProcess(FakeProcess):
    def __init__(self):
        super().__init__([{"type": "turn.completed", "turn_id": "t1"}])
        self.returncode = None
        self.wait_timeouts = []
        self.kill_calls = 0

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        raise delegate.subprocess.TimeoutExpired("fake-codex", timeout)

    def kill(self):
        self.kill_calls += 1


class DelegateTransportContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"; self.workspace.mkdir()
        self.task = self.root / "task.txt"; self.task.write_text("inspect only", encoding="utf-8")
        self.calls = []; self.processes = []

    def tearDown(self): self.tmp.cleanup()

    def args(self, **overrides):
        values = dict(model="gpt-test", preset=None, transport="cli", task_file=str(self.task),
                      workspace=str(self.workspace), write=False, effort=None,
                      codex_executable="fake-codex", config=None, resume_identity=None)
        values.update(overrides)
        return argparse.Namespace(**values)

    def fake(self, streams):
        def popen(argv, **kwargs):
            self.calls.append((argv, kwargs))
            proc = FakeProcess(streams.pop(0)); self.processes.append(proc)
            return proc
        return popen

    def test_cli_argv_and_stdin_are_exact_for_read_only_and_write(self):
        for write, sandbox in ((False, "read-only"), (True, "workspace-write")):
            with self.subTest(write=write):
                streams = [[{"type": "thread.started", "thread_id": "s1"},
                            {"type": "turn.completed", "turn_id": "t1"}]]
                result, code = delegate.run_delegate(self.args(write=write), catalog_payload=CATALOG,
                                                     popen_factory=self.fake(streams), environ={})
                self.assertEqual(code, 0); self.assertEqual(result["sandbox"], sandbox)
                argv, kwargs = self.calls[-1]
                self.assertEqual(argv, ["fake-codex", "exec", "-m", "gpt-test", "-c",
                                        "model_reasoning_effort=medium", "-s", sandbox, "-C",
                                        str(self.workspace.resolve()), "--json", "-"])
                self.assertNotIn("--worktree", argv); self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)
                self.assertEqual(self.processes[-1].stdin.getvalue(), b"inspect only")
                self.assertEqual(kwargs["stderr"], delegate.subprocess.DEVNULL)
                self.assertGreaterEqual(self.processes[-1].wait_calls, 1)

    def test_app_server_lifecycle_uses_actual_sandbox_and_policy(self):
        streams = [[{"id": 1, "result": {}}, {"id": 2, "result": CATALOG}], [
            {"method": "server/notice", "params": {"phase": "boot"}},
            {"id": 1, "result": {}}, {"method": "server/notice", "params": {"phase": "catalog"}},
            {"id": 2, "result": CATALOG},
            {"id": 3, "result": {"thread": {"id": "th1"}}},
            {"id": 4, "result": {"turn": {"id": "tu1"}}},
            {"method": "turn/updated", "params": {"turn": {"id": "tu1", "status": "completed"}}},
        ]]
        result, code = delegate.run_delegate(self.args(transport="app-server", write=True),
                                             popen_factory=self.fake(streams), environ={})
        self.assertEqual(code, 0); self.assertEqual(result["thread_id"], "th1")
        argv, kwargs = self.calls[1]; self.assertEqual(argv, ["fake-codex", "app-server", "--stdio"])
        sent = [json.loads(line) for line in self.processes[1].stdin.getvalue().decode().splitlines()]
        self.assertEqual([x.get("method") for x in sent], ["initialize", "initialized", "model/list", "thread/start", "turn/start"])
        self.assertEqual(sent[0]["params"], {"clientInfo": {"name": "model-proctor", "version": "1"}, "capabilities": {}})
        self.assertEqual(sent[3]["params"]["model"], "gpt-test")
        self.assertEqual(sent[3]["params"]["sandbox"], "workspace-write")
        self.assertEqual(sent[4]["params"]["threadId"], "th1")
        self.assertEqual((sent[4]["params"]["model"], sent[4]["params"]["effort"]), ("gpt-test", "medium"))
        self.assertEqual(sent[4]["params"]["sandboxPolicy"], {"type": "workspaceWrite", "networkAccess": False})
        self.assertEqual(sent[4]["params"]["input"], [{"type": "text", "text": "inspect only"}])

    def test_invalid_catalog_selection_refuses_without_launch(self):
        for args in (self.args(model="unknown"), self.args(effort="ultra"), self.args(model=None, preset="missing")):
            with self.subTest(args=args):
                result, code = delegate.run_delegate(args, catalog_payload=CATALOG,
                                                     popen_factory=self.fake([]), environ={})
                self.assertEqual(code, delegate.EXIT_INVALID); self.assertEqual(self.calls, [])
                self.assertEqual(result["status"], "invalid")

    def test_transport_errors_and_missing_terminal_do_not_fallback(self):
        for lines in ([["not-json"], [{"type": "thread.started", "thread_id": "s1"}]]):
            with self.subTest(lines=lines):
                before = len(self.calls)
                result, code = delegate.run_delegate(self.args(), catalog_payload=CATALOG,
                                                     popen_factory=self.fake([lines]), environ={})
                self.assertEqual(code, delegate.EXIT_OPERATIONAL); self.assertEqual(len(self.calls), before + 1)
                self.assertEqual(result["status"], "operational_failure")

    def test_post_kill_timeout_returns_operational_envelope_with_selected_binding(self):
        proc = UnreapableProcess()
        result, code = delegate.run_delegate(
            self.args(write=True, effort="low"), catalog_payload=CATALOG,
            popen_factory=lambda *args, **kwargs: proc, environ={})
        self.assertEqual(code, delegate.EXIT_OPERATIONAL)
        self.assertEqual({key: result[key] for key in (
            "schema_version", "status", "model", "effort", "transport", "sandbox", "usage")},
            {"schema_version": 1, "status": "operational_failure", "model": "gpt-test",
             "effort": "low", "transport": "cli", "sandbox": "workspace-write", "usage": "unknown"})
        self.assertTrue(result["error"])
        self.assertEqual(proc.wait_timeouts, [1, 1])
        self.assertEqual(proc.kill_calls, 1)

    def test_main_emits_one_bound_envelope_after_post_kill_timeout(self):
        proc = UnreapableProcess()
        run_delegate = delegate.run_delegate
        output = io.StringIO()
        argv = ["delegate", "--model", "gpt-test", "--transport", "cli",
                "--task-file", str(self.task), "--workspace", str(self.workspace)]
        with patch.object(sys, "argv", argv), patch.object(
                delegate, "run_delegate", side_effect=lambda args: run_delegate(
                    args, catalog_payload=CATALOG, popen_factory=lambda *args, **kwargs: proc,
                    environ={})), redirect_stdout(output), self.assertRaises(SystemExit) as exit_result:
            delegate.main()
        self.assertEqual(exit_result.exception.code, delegate.EXIT_OPERATIONAL)
        result = json.loads(output.getvalue())
        self.assertEqual((result["status"], result["model"], result["effort"],
                          result["transport"], result["sandbox"]),
                         ("operational_failure", "gpt-test", "medium", "cli", "read-only"))
        self.assertEqual(proc.wait_timeouts, [1, 1])
        self.assertEqual(proc.kill_calls, 1)

    def test_child_marker_and_nested_event_refuse(self):
        result, code = delegate.run_delegate(self.args(), catalog_payload=CATALOG,
                                             popen_factory=self.fake([]), environ={"PROCTOR_CHILD": "1"})
        self.assertEqual(code, delegate.EXIT_INVALID); self.assertEqual(self.calls, [])
        for event_type in ("collabAgentToolCall", "subAgentActivity"):
            with self.subTest(event_type=event_type):
                streams = [[{"type": event_type}, {"type": "turn.completed"}]]
                result, code = delegate.run_delegate(self.args(), catalog_payload=CATALOG,
                                                     popen_factory=self.fake(streams), environ={})
                self.assertEqual(code, delegate.EXIT_OPERATIONAL); self.assertTrue(result["nested_dispatch_detected"])
                self.assertEqual(result["status"], "refused")

    def test_cli_resume_is_refused_even_with_a_bound_identity(self):
        identity = json.dumps({"transport": "cli", "model": "gpt-test", "effort": "medium",
                               "sandbox": "read-only", "session_id": "session-7"})
        result, code = delegate.run_delegate(self.args(resume_identity=identity), catalog_payload=CATALOG,
                                             popen_factory=self.fake([]), environ={})
        self.assertEqual(code, delegate.EXIT_INVALID); self.assertEqual(self.calls, [])

    def test_app_server_resume_reapplies_binding_and_uses_returned_thread_id(self):
        identity = json.dumps({"transport": "app-server", "model": "gpt-test", "effort": "medium",
                               "sandbox": "read-only", "thread_id": "old-thread"})
        streams = [[{"id": 1, "result": {}}, {"id": 2, "result": CATALOG}], [
            {"id": 1, "result": {}}, {"id": 2, "result": CATALOG},
            {"id": 3, "result": {"thread": {"id": "resumed-thread"}}}, {"id": 4, "result": {}},
            {"method": "turn/updated", "params": {"turn": {"id": "turn-2", "status": "completed"}}},
        ]]
        result, code = delegate.run_delegate(self.args(transport="app-server", resume_identity=identity),
                                             popen_factory=self.fake(streams), environ={})
        self.assertEqual(code, 0); sent = [json.loads(line) for line in self.processes[1].stdin.getvalue().decode().splitlines()]
        self.assertEqual(sent[3]["method"], "thread/resume")
        self.assertEqual(sent[3]["params"], {"threadId": "old-thread", "cwd": str(self.workspace.resolve()), "model": "gpt-test", "sandbox": "read-only"})
        self.assertEqual((sent[4]["params"]["threadId"], sent[4]["params"]["model"], sent[4]["params"]["effort"]), ("resumed-thread", "gpt-test", "medium"))

    def test_in_session_catalog_drift_refuses_before_thread_work(self):
        drifted = {"data": [{"id": "gpt-other", "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]}]}
        streams = [[{"id": 1, "result": {}}, {"id": 2, "result": CATALOG}], [{"id": 1, "result": {}}, {"id": 2, "result": drifted}]]
        result, code = delegate.run_delegate(self.args(transport="app-server"), popen_factory=self.fake(streams), environ={})
        self.assertEqual(code, delegate.EXIT_OPERATIONAL); self.assertEqual(result["status"], "operational_failure")
        sent = [json.loads(line) for line in self.processes[1].stdin.getvalue().decode().splitlines()]
        self.assertEqual([item["method"] for item in sent], ["initialize", "initialized", "model/list"])

    def test_failed_and_interrupted_turns_are_not_successes(self):
        for terminal in ("failed", "interrupted"):
            with self.subTest(terminal=terminal):
                streams = [[{"type": f"turn.{terminal}", "turn_id": "t1"}]]
                result, code = delegate.run_delegate(self.args(), catalog_payload=CATALOG, popen_factory=self.fake(streams), environ={})
                self.assertEqual((result["status"], code), (terminal, delegate.EXIT_OPERATIONAL))

    def test_envelope_has_evidence_ids_and_never_cost_or_kimi_claim(self):
        streams = [[{"type": "thread.started", "thread_id": "s1"}, {"type": "turn.completed", "turn_id": "t1"}]]
        result, code = delegate.run_delegate(self.args(), catalog_payload=CATALOG,
                                             popen_factory=self.fake(streams), environ={})
        self.assertEqual(code, 0); self.assertEqual(result["usage"], "unknown")
        self.assertEqual((result["session_id"], result["turn_id"]), ("s1", "t1"))
        self.assertTrue(result["terminal_evidence"])
        self.assertNotIn("cost", result); self.assertNotIn("kimi_acceptance", result)

    def test_concrete_provider_usage_is_preserved(self):
        usage = {"input_tokens": 12, "output_tokens": 3}
        streams = [[{"type": "turn.completed", "turn_id": "t1", "usage": usage}]]
        result, code = delegate.run_delegate(self.args(), catalog_payload=CATALOG, popen_factory=self.fake(streams), environ={})
        self.assertEqual(code, 0); self.assertEqual(result["usage"], usage); self.assertNotIn("cost", result)

    def test_schema_shaped_token_usage_notification_is_preserved(self):
        streams = [[{"id": 1, "result": {}}, {"id": 2, "result": CATALOG}], [
            {"id": 1, "result": {}}, {"id": 2, "result": CATALOG},
            {"id": 3, "result": {"thread": {"id": "th1"}}}, {"id": 4, "result": {}},
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "th1", "turnId": "t1", "tokenUsage": TOKEN_USAGE}},
            {"method": "turn/updated", "params": {"turn": {"id": "t1", "status": "completed"}}},
        ]]
        result, code = delegate.run_delegate(self.args(transport="app-server"), popen_factory=self.fake(streams), environ={})
        self.assertEqual(code, 0); self.assertEqual(result["usage"], TOKEN_USAGE)

    def test_latest_token_usage_notification_wins(self):
        latest = dict(TOKEN_USAGE, modelContextWindow=256000)
        streams = [[{"id": 1, "result": {}}, {"id": 2, "result": CATALOG}], [
            {"id": 1, "result": {}}, {"id": 2, "result": CATALOG},
            {"id": 3, "result": {"thread": {"id": "th1"}}}, {"id": 4, "result": {}},
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "th1", "turnId": "t1", "tokenUsage": TOKEN_USAGE}},
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "th1", "turnId": "t1", "tokenUsage": latest}},
            {"method": "turn/updated", "params": {"turn": {"id": "t1", "status": "completed"}}},
        ]]
        result, code = delegate.run_delegate(self.args(transport="app-server"), popen_factory=self.fake(streams), environ={})
        self.assertEqual(code, 0); self.assertEqual(result["usage"], latest)

    def test_server_request_is_refused_and_receives_json_rpc_error(self):
        request = {"jsonrpc": "2.0", "id": "approval-7", "method": "item/commandExecution/requestApproval",
                   "params": {"command": "do not run"}}
        streams = [[{"id": 1, "result": {}}, {"id": 2, "result": CATALOG}], [
            {"id": 1, "result": {}}, {"id": 2, "result": CATALOG}, request,
        ]]
        result, code = delegate.run_delegate(self.args(transport="app-server"), popen_factory=self.fake(streams), environ={})
        self.assertEqual((result["status"], code), ("operational_failure", delegate.EXIT_OPERATIONAL))
        sent = [json.loads(line) for line in self.processes[1].stdin.getvalue().decode().splitlines()]
        self.assertEqual(sent[-1], {"jsonrpc": "2.0", "id": "approval-7", "error": {"code": -32000, "message": "server request refused by bounded delegate"}})


if __name__ == "__main__": unittest.main()
