"""Deterministic contract tests for the bounded Codex transport adapter."""
import argparse
import io
import json
import os
import queue
import sys
import tempfile
import threading
import time
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
    def __init__(self, lines=None):
        super().__init__(lines if lines is not None else [{"type": "turn.completed", "turn_id": "t1"}])
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

    def app_events(self):
        return [{"id": 1, "result": {}}, {"id": 2, "result": CATALOG},
                {"id": 3, "result": {"thread": {"id": "th1"}}},
                {"id": 4, "result": {"turn": {"id": "tu1"}}},
                {"method": "turn/completed", "params": {"threadId": "th1",
                 "turn": {"id": "tu1", "status": "completed"}}}]

    def assert_bound_failure(self, result, code, transport="cli"):
        self.assertEqual((code, result["status"], result["model"], result["effort"],
                          result["transport"], result["sandbox"]),
                         (delegate.EXIT_OPERATIONAL, "operational_failure", "gpt-test",
                          "medium", transport, "read-only"))

    def test_timeout_defaults_and_adapter_only_cli_option(self):
        """Timeout must be finite by default and must never change child argv."""
        parser = delegate.build_parser()
        argv = ["--model", "gpt-test", "--transport", "cli", "--task-file", str(self.task),
                "--workspace", str(self.workspace)]
        self.assertEqual(getattr(parser.parse_args(argv), "timeout", None), 1800)
        args = parser.parse_args(argv + ["--timeout", "7200", "--codex-executable", "fake-codex"])
        result, code = delegate.run_delegate(args, catalog_payload=CATALOG,
            popen_factory=self.fake([[{"type": "turn.completed"}]]), environ={})
        self.assertEqual((code, result["status"]), (0, "completed"))
        self.assertEqual(self.calls[0][0], ["fake-codex", "exec", "-m", "gpt-test", "-c",
            "model_reasoning_effort=medium", "-s", "read-only", "-C",
            str(self.workspace.resolve()), "--json", "-"])

    def test_invalid_timeout_refuses_before_any_process(self):
        """NaN, infinities, non-positive values and over-cap timeouts are invalid."""
        for timeout in (float("nan"), float("inf"), -float("inf"), 0, -1, 7200.01):
            with self.subTest(timeout=timeout):
                self.calls.clear()
                result, code = delegate.run_delegate(self.args(timeout=timeout), catalog_payload=CATALOG,
                    popen_factory=self.fake([[{"type": "turn.completed"}]]), environ={})
                self.assertEqual((code, result["status"], self.calls),
                                 (delegate.EXIT_INVALID, "invalid", []))

    def test_cli_stdout_deadline_is_enforced_before_process_wait(self):
        """An expired stdout read cannot be converted into completed by wait()."""
        clock = [0.0]
        proc = FakeProcess([{"type": "thread.started", "thread_id": "s1"},
                            {"type": "turn.completed", "turn_id": "t1"}])
        class ExpiringStream(io.BytesIO):
            def read(self, *args):
                clock[0] = 2.0
                return super().read(*args)
            def readline(self, *args):
                line = super().readline(*args)
                if b"turn.completed" in line:
                    clock[0] = 2.0
                return line
        proc.stdout = ExpiringStream(proc.stdout.getvalue())
        with patch.object(time, "monotonic", side_effect=lambda: clock[0]):
            result, code = delegate.run_delegate(self.args(timeout=1), catalog_payload=CATALOG,
                popen_factory=lambda *args, **kwargs: proc, environ={})
        self.assert_bound_failure(result, code)
        self.assertEqual(result["error"], "TransportTimeout")
        self.assertEqual(result["session_id"], "s1")

    def test_every_app_server_wait_uses_the_deadline(self):
        """Any stalled lifecycle response or terminal wait must fail boundedly."""
        for expired_line in range(1, 6):
            with self.subTest(expired_line=expired_line):
                clock = [0.0]
                proc = FakeProcess(self.app_events())
                class ExpiringStream(io.BytesIO):
                    count = 0
                    def readline(self, *args):
                        self.count += 1
                        line = super().readline(*args)
                        if self.count == expired_line:
                            clock[0] = 2.0
                        return line
                proc.stdout = ExpiringStream(proc.stdout.getvalue())
                with patch.object(time, "monotonic", side_effect=lambda: clock[0]):
                    result, code = delegate.run_delegate(self.args(transport="app-server", timeout=1),
                        catalog_payload=CATALOG, popen_factory=lambda *args, **kwargs: proc, environ={})
                self.assert_bound_failure(result, code, "app-server")
                self.assertEqual(result["error"], "TransportTimeout")

    def test_preflight_and_worker_share_one_deadline(self):
        """Starting the worker must not reset the preflight's elapsed timeout."""
        clock = [0.0]
        streams = [[{"id": 1, "result": {}}, {"id": 2, "result": CATALOG}],
                   [{"type": "thread.started", "thread_id": "s1"}, {"type": "turn.completed"}]]
        class ElapsedStream(io.BytesIO):
            def read(self, *args):
                clock[0] += 0.6
                return super().read(*args)
            def readline(self, *args):
                clock[0] += 0.3
                return super().readline(*args)
        def popen(*args, **kwargs):
            proc = FakeProcess(streams.pop(0))
            proc.stdout = ElapsedStream(proc.stdout.getvalue())
            return proc
        with patch.object(time, "monotonic", side_effect=lambda: clock[0]):
            result, code = delegate.run_delegate(self.args(timeout=1), popen_factory=popen, environ={})
        self.assert_bound_failure(result, code)
        self.assertEqual(result["error"], "TransportTimeout")

    def test_stalled_io_worker_times_out_without_wall_clock_sleep(self):
        """A pipe operation that never returns must still have a finite wait."""
        for transport, stalled_call in (("cli", 1), ("cli", 2), ("app-server", 2),
                                        ("app-server", 5), ("app-server", 7),
                                        ("app-server", 9), ("app-server", 10)):
            with self.subTest(transport=transport, stalled_call=stalled_call):
                calls = [0]
                wait_budgets = []
                real_get = queue.Queue.get
                class ControlledThread:
                    def __init__(self, target, *, daemon):
                        self.target = target
                    def start(self):
                        calls[0] += 1
                        # All fake I/O completes inline except the deliberately stalled operation.
                        if calls[0] != stalled_call:
                            self.target()
                def immediate_get(queued, block=True, timeout=None):
                    wait_budgets.append(timeout)
                    return real_get(queued, block=False)
                proc = FakeProcess([{"type": "turn.completed"}] if transport == "cli" else self.app_events())
                with patch.object(threading, "Thread", ControlledThread), patch.object(queue.Queue, "get", immediate_get):
                    result, code = delegate.run_delegate(self.args(transport=transport, timeout=5),
                        catalog_payload=CATALOG, popen_factory=lambda *args, **kwargs: proc, environ={})
                self.assert_bound_failure(result, code, transport)
                self.assertEqual(result["error"], "TransportTimeout")
                self.assertTrue(wait_budgets)
                self.assertTrue(all(budget is not None and 0 < budget <= 5 for budget in wait_budgets))
                self.assertGreater(proc.wait_calls, 0)

    def test_nonzero_cli_exit_retains_terminal_and_ids_as_failure(self):
        """An apparent terminal event does not override a failing child exit."""
        terminal = {"type": "turn.completed", "turn_id": "t1"}
        proc = FakeProcess([{"type": "thread.started", "thread_id": "s1"}, terminal])
        proc.returncode = 9
        proc.wait = lambda timeout=None: 9
        result, code = delegate.run_delegate(self.args(), catalog_payload=CATALOG,
            popen_factory=lambda *args, **kwargs: proc, environ={})
        self.assert_bound_failure(result, code)
        self.assertEqual((result["session_id"], result["turn_id"], result["terminal_evidence"]),
                         ("s1", "t1", [terminal]))

    def test_broken_stdin_is_cleaned_up_and_normalized(self):
        """A stdin failure must still reap the selected transport process."""
        for transport in ("cli", "app-server"):
            with self.subTest(transport=transport):
                proc = FakeProcess([])
                proc.stdin.write = lambda data: (_ for _ in ()).throw(BrokenPipeError("private detail"))
                result, code = delegate.run_delegate(self.args(transport=transport), catalog_payload=CATALOG,
                    popen_factory=lambda *args, **kwargs: proc, environ={})
                self.assert_bound_failure(result, code, transport)
                self.assertGreater(proc.wait_calls, 0)
                self.assertNotIn("private detail", result["error"])

    def test_unreaped_app_session_cannot_report_completed(self):
        """Swallowing post-kill wait failure must not turn cleanup into success."""
        proc = UnreapableProcess(self.app_events())
        result, code = delegate.run_delegate(self.args(transport="app-server"), catalog_payload=CATALOG,
            popen_factory=lambda *args, **kwargs: proc, environ={})
        self.assert_bound_failure(result, code, "app-server")
        self.assertEqual((result["thread_id"], result["turn_id"]), ("th1", "tu1"))
        self.assertEqual(result["terminal_evidence"], [self.app_events()[-1]])
        self.assertEqual(proc.wait_timeouts, [1, 1])
        self.assertEqual(proc.kill_calls, 1)

    def test_timeout_error_class_survives_failed_cleanup(self):
        """A second failure while reaping must not hide an expired deadline."""
        for transport in ("cli", "app-server"):
            with self.subTest(transport=transport):
                clock = [0.0]
                proc = UnreapableProcess(self.app_events() if transport == "app-server" else None)
                class ExpiringStream(io.BytesIO):
                    def readline(self, *args):
                        line = super().readline(*args)
                        clock[0] = 2.0
                        return line
                proc.stdout = ExpiringStream(proc.stdout.getvalue())
                with patch.object(time, "monotonic", side_effect=lambda: clock[0]):
                    result, code = delegate.run_delegate(self.args(transport=transport, timeout=1),
                        catalog_payload=CATALOG, popen_factory=lambda *args, **kwargs: proc, environ={})
                self.assert_bound_failure(result, code, transport)
                self.assertEqual(result["error"], "TransportTimeout")
                self.assertEqual(proc.wait_timeouts, [1, 1])
                self.assertEqual(proc.kill_calls, 1)

    def test_rpc_error_stops_at_the_failed_request(self):
        """An RPC error must terminate without issuing the next lifecycle request."""
        methods = ("initialize", "model/list", "thread/start", "turn/start")
        for index, method in enumerate(methods):
            with self.subTest(method=method):
                self.calls.clear(); self.processes.clear()
                events = self.app_events()
                events[index] = {"id": index + 1, "error": {"code": -1, "message": "private detail"}}
                result, code = delegate.run_delegate(self.args(transport="app-server"), catalog_payload=CATALOG,
                    popen_factory=self.fake([events]), environ={})
                self.assert_bound_failure(result, code, "app-server")
                sent = [json.loads(line) for line in self.processes[0].stdin.getvalue().splitlines()]
                self.assertEqual(sent[-1]["method"], method)
                self.assertNotIn("private detail", result["error"])

    def test_only_matching_thread_and_turn_can_complete_app_attempt(self):
        """Unrelated terminal notifications cannot complete the selected turn."""
        for other_thread, other_turn in (("other", "tu1"), ("th1", "other")):
            for matching_follows in (False, True):
                with self.subTest(thread=other_thread, turn=other_turn, matching=matching_follows):
                    events = self.app_events()[:-1]
                    events.append({"method": "turn/completed", "params": {"threadId": other_thread,
                                   "turn": {"id": other_turn, "status": "completed"}}})
                    if matching_follows:
                        events.append({"method": "turn/completed", "params": {"threadId": "th1",
                                       "turn": {"id": "tu1", "status": "failed"}}})
                    result, code = delegate.run_delegate(self.args(transport="app-server"), catalog_payload=CATALOG,
                        popen_factory=self.fake([events]), environ={})
                    self.assertEqual((code, result["status"], result["thread_id"], result["turn_id"]),
                                     (delegate.EXIT_OPERATIONAL, "failed" if matching_follows else "operational_failure",
                                      "th1", "tu1"))

    def test_rpc_error_during_terminal_wait_emits_one_bound_failure(self):
        """An error after turn acknowledgement must defeat a later matching terminal."""
        for response_id in (4, "other-response", None):
            with self.subTest(response_id=response_id):
                prior_terminal = {"method": "turn/completed", "params": {
                    "threadId": "other-thread", "turn": {"id": "other-turn", "status": "completed"}}}
                events = self.app_events()
                events[-1:-1] = [prior_terminal,
                    {"method": "thread/tokenUsage/updated", "params": {
                        "threadId": "th1", "turnId": "tu1", "tokenUsage": TOKEN_USAGE}},
                    {"jsonrpc": "2.0", "id": response_id,
                     "error": {"code": -32000, "message": "private server detail"}}]
                proc = FakeProcess(events)
                run = delegate.run_delegate
                output = io.StringIO()
                with patch.object(sys, "argv", ["delegate", "--model", "gpt-test",
                        "--transport", "app-server", "--workspace", str(self.workspace),
                        "--task-file", str(self.task)]), patch.object(delegate, "run_delegate",
                        side_effect=lambda args: run(args, catalog_payload=CATALOG,
                            popen_factory=lambda *args, **kwargs: proc, environ={})), \
                        redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
                    delegate.main()
                self.assertEqual(len(output.getvalue().splitlines()), 1)
                result = json.loads(output.getvalue())
                self.assert_bound_failure(result, stopped.exception.code, "app-server")
                self.assertEqual((result["thread_id"], result["turn_id"], result["usage"],
                                  result["terminal_evidence"], result["error"]),
                                 ("th1", "tu1", TOKEN_USAGE, [prior_terminal], "RpcFailure"))

    def test_server_request_during_terminal_wait_is_refused(self):
        """A server request still receives its refusal response while waiting for a turn."""
        events = self.app_events()
        events.insert(-1, {"jsonrpc": "2.0", "id": "approval-7",
            "method": "item/commandExecution/requestApproval", "params": {"command": "do not run"}})
        result, code = delegate.run_delegate(self.args(transport="app-server"),
            catalog_payload=CATALOG, popen_factory=self.fake([events]), environ={})
        self.assert_bound_failure(result, code, "app-server")
        self.assertEqual(result["error"], "ValueError")
        sent = [json.loads(line) for line in self.processes[0].stdin.getvalue().splitlines()]
        self.assertEqual(sent[-1], {"jsonrpc": "2.0", "id": "approval-7", "error": {
            "code": -32000, "message": "server request refused by bounded delegate"}})

    def test_matching_terminal_before_turn_response_is_retained(self):
        """A terminal notification racing the response must remain matchable."""
        events = self.app_events()
        events[-2:] = [events[-1], events[-2]]
        result, code = delegate.run_delegate(self.args(transport="app-server"), catalog_payload=CATALOG,
            popen_factory=self.fake([events]), environ={})
        self.assertEqual((code, result["status"], result["thread_id"], result["turn_id"]),
                         (0, "completed", "th1", "tu1"))
        self.assertEqual(result["terminal_evidence"], [events[-2]])

    def test_nested_thread_id_cannot_substitute_for_terminal_turn_id(self):
        """Matching must use the turn ID, not an unrelated nested object's ID."""
        events = self.app_events()
        events[-1]["params"]["thread"] = {"id": "tu1"}
        events[-1]["params"]["turn"]["id"] = "other-turn"
        result, code = delegate.run_delegate(self.args(transport="app-server"), catalog_payload=CATALOG,
            popen_factory=self.fake([events]), environ={})
        self.assert_bound_failure(result, code, "app-server")
        self.assertEqual(result["turn_id"], "tu1")

    def test_missing_turn_response_id_cannot_be_inferred_from_a_notification(self):
        """Turn identity must come from turn/start before matching a terminal."""
        events = self.app_events()
        events[3] = {"id": 4, "result": {}}
        result, code = delegate.run_delegate(self.args(transport="app-server"), catalog_payload=CATALOG,
            popen_factory=self.fake([events]), environ={})
        self.assert_bound_failure(result, code, "app-server")

    def test_turn_start_rejects_surrogate_turn_ids(self):
        """Only direct result.turn.id may authorize terminal matching."""
        for container in ("result", "params", "arguments"):
            for early in (None, "direct", "surrogate"):
                with self.subTest(container=container, early=early):
                    events = self.app_events()
                    events[3]["result"]["turn"] = {container: {"id": "tu1"}}
                    if early == "surrogate":
                        events[-1]["params"]["turn"] = {
                            "status": "completed", container: {"id": "tu1"}}
                    if early:
                        events[-2:] = [events[-1], events[-2]]
                    result, code = delegate.run_delegate(self.args(transport="app-server"),
                        catalog_payload=CATALOG, popen_factory=self.fake([events]), environ={})
                    self.assert_bound_failure(result, code, "app-server")
                    self.assertEqual(result["thread_id"], "th1")
                    self.assertEqual(result["turn_id"], "tu1" if early == "direct" else None)
                    self.assertEqual(result["terminal_evidence"], [events[-2]] if early else [])

    def test_terminal_surrogate_turn_ids_do_not_match(self):
        """Surrogate terminal IDs must not complete either side of the response race."""
        for container in ("result", "params", "arguments"):
            for early in (False, True):
                for matching_follows in (False, True):
                    with self.subTest(container=container, early=early, matching=matching_follows):
                        events = self.app_events()
                        events[-1]["params"]["turn"] = {
                            "status": "completed", container: {"id": "tu1"}}
                        surrogate = events[-1]
                        if early:
                            events[-2:] = [events[-1], events[-2]]
                        if matching_follows:
                            events.append({"method": "turn/completed", "params": {"threadId": "th1",
                                "turn": {"id": "tu1", "status": "failed"}}})
                        result, code = delegate.run_delegate(self.args(transport="app-server"),
                            catalog_payload=CATALOG, popen_factory=self.fake([events]), environ={})
                        self.assertEqual((code, result["status"], result["thread_id"], result["turn_id"]),
                            (delegate.EXIT_OPERATIONAL, "failed" if matching_follows else "operational_failure",
                             "th1", "tu1"))
                        self.assertEqual(result["terminal_evidence"],
                            [surrogate, events[-1]] if matching_follows else [surrogate])

    def test_empty_child_marker_refuses_without_launch(self):
        """Clearing the marker value must not bypass the child-key guard."""
        result, code = delegate.run_delegate(self.args(), catalog_payload=CATALOG,
            popen_factory=self.fake([[{"type": "turn.completed"}]]), environ={"PROCTOR_CHILD": ""})
        self.assertEqual((code, result["status"], self.calls), (delegate.EXIT_INVALID, "refused", []))

    def test_malformed_cli_syntax_preserves_already_parsed_evidence(self):
        """A later JSON parse error cannot erase a terminal or nesting observation."""
        terminal = {"type": "turn.completed", "turn_id": "t1"}
        proc = FakeProcess([{"type": "thread.started", "thread_id": "s1"},
                            {"type": "collabAgentToolCall"}, terminal])
        proc.stdout = io.BytesIO(proc.stdout.getvalue() + b'{"broken":\n')
        result, code = delegate.run_delegate(self.args(), catalog_payload=CATALOG,
            popen_factory=lambda *args, **kwargs: proc, environ={})
        self.assert_bound_failure(result, code)
        self.assertEqual((result["session_id"], result["turn_id"], result["terminal_evidence"],
                          result["nested_dispatch_detected"]), ("s1", "t1", [terminal], True))

    def test_malformed_app_syntax_preserves_already_parsed_evidence(self):
        """Malformed response syntax after early events must retain those events."""
        events = self.app_events()
        terminal = events[-1]
        proc = FakeProcess(events[:3] + [{"method": "item/updated", "params": {
            "item": {"type": "subAgentActivity"}}}, terminal])
        proc.stdout = io.BytesIO(proc.stdout.getvalue() + b'{"id":4,"result":\n')
        result, code = delegate.run_delegate(self.args(transport="app-server"), catalog_payload=CATALOG,
            popen_factory=lambda *args, **kwargs: proc, environ={})
        self.assert_bound_failure(result, code, "app-server")
        self.assertEqual((result["thread_id"], result["turn_id"], result["terminal_evidence"],
                          result["nested_dispatch_detected"]), ("th1", "tu1", [terminal], True))

    def test_unexpected_json_shapes_emit_one_selected_binding_envelope(self):
        """Unexpected JSON types must not fall through main and erase selection."""
        for transport, malformed in (("cli", {"type": []}), ("cli", {"type": {}}),
                ("cli", {"type": "item.completed", "item": {"type": []}}),
                ("app-server", {"method": "turn/completed", "params": {
                    "threadId": "th1", "turn": {"id": "tu1", "status": []}}}),
                ("app-server", {"method": [], "params": {
                    "threadId": "th1", "turn": {"id": "tu1", "status": "completed"}}})):
            with self.subTest(transport=transport, malformed=malformed):
                lines = ([{"type": "thread.started", "thread_id": "s1"}, malformed,
                          {"type": "turn.completed"}] if transport == "cli" else self.app_events()[:-1] + [malformed])
                proc = FakeProcess(lines)
                run = delegate.run_delegate
                output = io.StringIO()
                with patch.object(sys, "argv", ["delegate", "--model", "gpt-test", "--transport", transport,
                        "--workspace", str(self.workspace), "--task-file", str(self.task)]), patch.object(
                        delegate, "run_delegate", side_effect=lambda args: run(args, catalog_payload=CATALOG,
                            popen_factory=lambda *args, **kwargs: proc, environ={})), redirect_stdout(output), \
                        self.assertRaises(SystemExit) as stopped:
                    delegate.main()
                self.assertEqual(len(output.getvalue().splitlines()), 1)
                result = json.loads(output.getvalue())
                self.assert_bound_failure(result, stopped.exception.code, transport)

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
            {"method": "turn/updated", "params": {"threadId": "th1", "turn": {"id": "tu1", "status": "completed"}}},
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

    def test_paginated_catalog_selects_later_page_terra_in_both_transports(self):
        """Dropping nextCursor pages must not hide an available selected model."""
        terra = {"id": "gpt-5.6-terra", "defaultReasoningEffort": "medium",
                 "supportedReasoningEfforts": [{"reasoningEffort": "medium"},
                                               {"reasoningEffort": "high"}]}
        pages = [{"id": 1, "result": {}},
                 {"id": 2, "result": dict(CATALOG, nextCursor="page-two")},
                 {"id": 3, "result": {"data": [terra], "nextCursor": None}}]
        for transport in ("cli", "app-server"):
            with self.subTest(transport=transport):
                self.calls.clear(); self.processes.clear()
                worker = ([{"type": "turn.completed", "turn_id": "tu1"}]
                          if transport == "cli" else pages + [
                              {"id": 4, "result": {"thread": {"id": "th1"}}},
                              {"id": 5, "result": {"turn": {"id": "tu1"}}},
                              {"method": "turn/completed", "params": {"threadId": "th1",
                               "turn": {"id": "tu1", "status": "completed"}}}])
                result, code = delegate.run_delegate(
                    self.args(model="gpt-5.6-terra", effort="high", transport=transport),
                    popen_factory=self.fake([pages, worker]), environ={})
                self.assertEqual((code, result["status"], result["model"], result["effort"]),
                                 (0, "completed", "gpt-5.6-terra", "high"))
                self.assertEqual(len(self.calls), 2)
                self.assertEqual(self.calls[1][0][1], "exec" if transport == "cli" else "app-server")
                for proc in self.processes[:1 if transport == "cli" else 2]:
                    sent = [json.loads(line) for line in proc.stdin.getvalue().splitlines()]
                    catalogs = [request for request in sent if request.get("method") == "model/list"]
                    self.assertEqual([request["params"] for request in catalogs],
                                     [{}, {"cursor": "page-two"}])

    def test_pagination_fault_launches_no_model_work(self):
        """Invalid or repeated cursors must refuse before CLI/thread launch."""
        for phase in ("preflight", "session"):
            for cursor in ("", 0, False, [], {}, "repeated"):
                with self.subTest(phase=phase, cursor=cursor):
                    self.calls.clear(); self.processes.clear()
                    responses = [{"id": 1, "result": {}},
                                 {"id": 2, "result": dict(CATALOG, nextCursor=cursor)}]
                    if cursor == "repeated":
                        responses.append({"id": 3, "result": {"data": [], "nextCursor": cursor}})
                    responses += [{"id": 3, "result": {"thread": {"id": "th1"}}},
                                  {"id": 4, "result": {"turn": {"id": "tu1"}}},
                                  {"method": "turn/completed", "params": {"threadId": "th1",
                                   "turn": {"id": "tu1", "status": "completed"}}}]
                    result, code = delegate.run_delegate(
                        self.args(transport="cli" if phase == "preflight" else "app-server"),
                        catalog_payload=None if phase == "preflight" else CATALOG,
                        popen_factory=self.fake([responses, [{"type": "turn.completed"}]]), environ={})
                    self.assertNotEqual(code, 0)
                    self.assertNotEqual(result["status"], "completed")
                    self.assertEqual(len(self.calls), 1)
                    self.assertEqual(self.calls[0][0], ["fake-codex", "app-server", "--stdio"])
                    sent = [json.loads(line) for line in self.processes[0].stdin.getvalue().splitlines()]
                    self.assertFalse(any(request.get("method", "").startswith(("thread/", "turn/"))
                                         for request in sent))

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

    def test_mcp_payloads_are_not_codex_discriminators(self):
        """MCP arguments and structured results may contain arbitrary type fields."""
        payloads = [{"type": None}, {"type": []}, {"type": {}},
                    {"type": "collabAgentToolCall"}, {"subAgentActivity": {"type": None}}]
        for field in ("arguments", "structuredContent"):
            for payload in payloads:
                with self.subTest(field=field, payload=payload):
                    item = {"id": "mcp-1", "type": "mcpToolCall", "server": "fixture",
                            "tool": "inspect", "status": "completed", "arguments": {},
                            "result": {"content": [], "structuredContent": None}, "error": None}
                    if field == "arguments":
                        item["arguments"] = payload
                    else:
                        item["result"]["structuredContent"] = payload
                    events = self.app_events()
                    events.insert(-1, {"method": "item/completed", "params": {
                        "threadId": "th1", "turnId": "tu1", "item": item}})
                    result, code = delegate.run_delegate(self.args(transport="app-server"),
                        catalog_payload=CATALOG, popen_factory=self.fake([events]), environ={})
                    self.assertEqual((code, result["status"], result["nested_dispatch_detected"]),
                                     (0, "completed", False))
                    self.assertEqual(result["terminal_evidence"], [events[-1]])

    def test_actual_codex_item_discriminators_still_refuse(self):
        """Restricting payload traversal must retain real item-level nesting evidence."""
        for event_type in ("collabAgentToolCall", "subAgentActivity"):
            for location in ("cli-item", "notification-item", "response-items", "terminal-items"):
                with self.subTest(event_type=event_type, location=location):
                    item = {"id": "nested-1", "type": event_type}
                    transport = "cli" if location == "cli-item" else "app-server"
                    events = self.app_events()
                    if location == "cli-item":
                        events = [{"type": "item.completed", "item": item}, {"type": "turn.completed"}]
                    elif location == "notification-item":
                        events.insert(-1, {"method": "item/completed", "params": {
                            "threadId": "th1", "turnId": "tu1", "item": item}})
                    elif location == "response-items":
                        events[3]["result"]["turn"]["items"] = [item]
                    else:
                        events[-1]["params"]["turn"]["items"] = [item]
                    result, code = delegate.run_delegate(self.args(transport=transport),
                        catalog_payload=CATALOG, popen_factory=self.fake([events]), environ={})
                    self.assertEqual((code, result["status"], result["nested_dispatch_detected"]),
                                     (delegate.EXIT_OPERATIONAL, "refused", True))

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
            {"id": 3, "result": {"thread": {"id": "resumed-thread"}}},
            {"id": 4, "result": {"turn": {"id": "turn-2"}}},
            {"method": "turn/updated", "params": {"threadId": "resumed-thread", "turn": {"id": "turn-2", "status": "completed"}}},
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
            {"id": 3, "result": {"thread": {"id": "th1"}}}, {"id": 4, "result": {"turn": {"id": "t1"}}},
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "th1", "turnId": "t1", "tokenUsage": TOKEN_USAGE}},
            {"method": "turn/updated", "params": {"threadId": "th1", "turn": {"id": "t1", "status": "completed"}}},
        ]]
        result, code = delegate.run_delegate(self.args(transport="app-server"), popen_factory=self.fake(streams), environ={})
        self.assertEqual(code, 0); self.assertEqual(result["usage"], TOKEN_USAGE)

    def test_latest_token_usage_notification_wins(self):
        latest = dict(TOKEN_USAGE, modelContextWindow=256000)
        streams = [[{"id": 1, "result": {}}, {"id": 2, "result": CATALOG}], [
            {"id": 1, "result": {}}, {"id": 2, "result": CATALOG},
            {"id": 3, "result": {"thread": {"id": "th1"}}}, {"id": 4, "result": {"turn": {"id": "t1"}}},
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "th1", "turnId": "t1", "tokenUsage": TOKEN_USAGE}},
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "th1", "turnId": "t1", "tokenUsage": latest}},
            {"method": "turn/updated", "params": {"threadId": "th1", "turn": {"id": "t1", "status": "completed"}}},
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
