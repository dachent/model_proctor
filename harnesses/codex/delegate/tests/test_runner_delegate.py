"""Runner-contract tests for the policy-free Codex delegate bridge.

The production breaks covered here are a lane selecting the wrong preset or
sandbox, the runner's task-file changing before reaching the Codex adapter,
and the bridge dropping native Codex evidence while normalizing its status.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


DELEGATE_DIR = Path(__file__).resolve().parent.parent
BRIDGE = DELEGATE_DIR / "runner_delegate.py"
AGENT_MAP = DELEGATE_DIR / "runner-agent-map.json"
REPO_ROOT = DELEGATE_DIR.parents[2]
RUNNER = REPO_ROOT / "harnesses" / "kimi-code" / "runner" / "runner.py"

BUGGY = "def sum_to_n(n):\n    return sum(range(1, n))\n"
FIXED = "def sum_to_n(n):\n    return sum(range(1, n + 1))\n"
CHECK = (
    "from math_utils import sum_to_n\n"
    "assert sum_to_n(5) == 15\n"
    "print('PASS')\n"
)


def load_bridge():
    spec = importlib.util.spec_from_file_location("_runner_delegate_under_test", BRIDGE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RunnerDelegateContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="codex-runner-delegate-"))
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.task_file = self.tmp / "runner-task.txt"
        self.task_file.write_text("Repair sum_to_n without changing this task text.", encoding="utf-8")
        self.config = self.tmp / "local-config.json"
        self.config.write_text('{"schema": 1, "presets": {}}', encoding="utf-8")
        self.codex = self.tmp / "codex.exe"
        self.codex.write_text("fixture", encoding="utf-8")
        self.bridge = load_bridge()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_runner_agents_bind_fixed_presets_and_write_boundaries(self):
        """Changing a lane's preset or write boundary must alter its child request."""
        expected = {
            "luna": ("luna", True),
            "terra": ("terra", False),
            "astra": ("astra", False),
        }
        for agent, (preset, write) in expected.items():
            with self.subTest(agent=agent):
                captured = {}

                def fake_run_delegate(args):
                    captured["args"] = args
                    return {"status": "completed", "session_id": f"{agent}-session",
                            "thread_id": None, "terminal_evidence": [{"type": "turn.completed"}],
                            "error": None}, 0

                args = argparse.Namespace(agent=agent, workspace=str(self.workspace),
                                          task_file=str(self.task_file), timeout=23.5)
                with patch.object(self.bridge, "_local_config", return_value=self.config), \
                     patch.object(self.bridge, "_desktop_codex_executable", return_value=self.codex), \
                     patch.object(self.bridge.delegate, "run_delegate", side_effect=fake_run_delegate):
                    envelope = self.bridge.run_runner_delegate(args)

                child = captured["args"]
                self.assertEqual(child.preset, preset)
                self.assertEqual(child.transport, "app-server")
                self.assertEqual(child.write, write)
                self.assertEqual(child.workspace, str(self.workspace))
                self.assertEqual(child.task_file, str(self.task_file))
                self.assertEqual(child.timeout, 23.5)
                self.assertEqual(child.config, str(self.config))
                self.assertEqual(child.codex_executable, str(self.codex))
                self.assertIsNone(child.model)
                self.assertIsNone(child.effort)
                self.assertIsNone(child.resume_identity)
                self.assertEqual(envelope["status"], "completed")
                self.assertEqual(envelope["child_session_id"], f"{agent}-session")
                self.assertIsNone(envelope["child_home"])

    def test_thread_id_is_aliased_and_transport_outcomes_stay_evidenced(self):
        """Dropping a thread ID or its transport failure would blind the runner."""
        args = argparse.Namespace(agent="terra", workspace=str(self.workspace),
                                  task_file=str(self.task_file), timeout=10.0)
        cases = (
            ("completed", None, "thread-native", "completed"),
            ("operational_failure", "TransportTimeout", "thread-native", "timeout"),
            ("refused", "bad input", None, "invalid"),
            ("operational_failure", "RpcFailure", None, "internal_error"),
        )
        for codex_status, error, thread_id, expected_status in cases:
            with self.subTest(codex_status=codex_status, error=error):
                result = {"status": codex_status, "error": error,
                          "session_id": None, "thread_id": thread_id,
                          "terminal_evidence": [{"type": "turn.failed"}],
                          "nested_dispatch_detected": False}
                with patch.object(self.bridge, "_local_config", return_value=self.config), \
                     patch.object(self.bridge, "_desktop_codex_executable", return_value=self.codex), \
                     patch.object(self.bridge.delegate, "run_delegate", return_value=(result, 70)), \
                     patch.object(self.bridge.time, "monotonic", side_effect=(100.0, 101.25)):
                    envelope = self.bridge.run_runner_delegate(args)

                self.assertEqual(envelope["status"], expected_status)
                self.assertEqual(envelope["child_session_id"], thread_id)
                self.assertEqual(envelope["duration_seconds"], 1.25)
                self.assertEqual(envelope["codex_status"], codex_status)
                self.assertEqual(envelope["codex_error"], error)
                self.assertEqual(envelope["terminal_evidence"], result["terminal_evidence"])

    def test_fixed_lane_map_selects_the_three_runner_agents(self):
        """A changed map would silently make the unchanged runner select another worker."""
        self.assertEqual(json.loads(AGENT_MAP.read_text(encoding="utf-8")), {
            "flash": "luna", "glm": "terra", "k3": "astra",
        })

    def test_malformed_runner_argv_emits_one_invalid_json_envelope(self):
        """An argparse usage error would leave the unchanged runner with no envelope."""
        result = subprocess.run(
            [sys.executable, str(BRIDGE), "--agent", "luna", "--workspace",
             str(self.workspace), "--task-file", str(self.task_file),
             "--timeout", "not-a-number"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["status"], "invalid")
        self.assertEqual(envelope["agent"], "luna")
        self.assertIsNone(envelope["child_session_id"])
        self.assertIsNone(envelope["child_home"])
        self.assertEqual(envelope["codex_error"], "ArgumentError")


class RunnerCompatibilityIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="codex-runner-integration-"))
        self.bridge_dir = self.tmp / "bridge"
        self.bridge_dir.mkdir()
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        (self.workspace / "math_utils.py").write_text(BUGGY, encoding="utf-8")
        (self.workspace / "check.py").write_text(CHECK, encoding="utf-8")
        self.task = self.tmp / "task.json"
        self.task.write_text(json.dumps({
            "task_id": "codex-runner-proof",
            "prompt": "Repair the supplied fixture.",
            "scope": ["math_utils.py"],
            "features": {"bounded": True, "known_location": True,
                         "objective_acceptance": True},
            "verifier": {"argv": [sys.executable, "check.py"]},
        }), encoding="utf-8")
        shutil.copy2(BRIDGE, self.bridge_dir / "runner_delegate.py")
        shutil.copy2(AGENT_MAP, self.bridge_dir / "runner-agent-map.json")
        (self.bridge_dir / "local-config.json").write_text(
            '{"schema": 1, "presets": {}}', encoding="utf-8")
        (self.bridge_dir / "delegate.py").write_text(
            "from pathlib import Path\n"
            "def run_delegate(args):\n"
            "    assert args.preset == 'luna' and args.write is True\n"
            "    assert args.transport == 'app-server'\n"
            "    task = Path(args.task_file)\n"
            "    assert task.is_file() and task.read_text(encoding='utf-8') == 'Repair the supplied fixture.'\n"
            "    Path(args.workspace, 'math_utils.py').write_text('def sum_to_n(n):\\n    return sum(range(1, n + 1))\\n', encoding='utf-8')\n"
            "    return {'status': 'completed', 'thread_id': 'native-thread-1', 'session_id': None,\n"
            "            'terminal_evidence': [{'type': 'turn.completed'}], 'error': None}, 0\n",
            encoding="utf-8",
        )
        self.local_app_data = self.tmp / "local-app-data"
        codex = self.local_app_data / "OpenAI" / "Codex" / "bin" / "fixture" / "codex.exe"
        codex.parent.mkdir(parents=True)
        codex.write_text("fixture", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_runner(self, *argv):
        environment = dict(os.environ, LOCALAPPDATA=str(self.local_app_data))
        result = subprocess.run([sys.executable, str(RUNNER), *argv], capture_output=True,
                                text=True, env=environment, timeout=120)
        return result.returncode, json.loads(result.stdout) if result.stdout else {}

    def test_unchanged_runner_completes_init_dispatch_verify_accept_record(self):
        """Runner compatibility breaks if its existing delegate invocation stops accepting this bridge."""
        common = ("--workspace", str(self.workspace), "--task", str(self.task))
        rc, init = self.run_runner("init", *common)
        self.assertEqual(rc, 0, init)
        rc, dispatch = self.run_runner("dispatch", *common, "--delegate",
                                       str(self.bridge_dir / "runner_delegate.py"), "--agent-map",
                                       str(self.bridge_dir / "runner-agent-map.json"))
        self.assertEqual(rc, 0, dispatch)
        self.assertEqual(dispatch["agent"], "luna")
        self.assertEqual(dispatch["child_session_id"], "native-thread-1")
        rc, verify = self.run_runner("verify", *common)
        self.assertEqual(rc, 0, verify)
        self.assertTrue(verify["passed"], verify)
        rc, accept = self.run_runner("accept", *common)
        self.assertEqual(rc, 0, accept)
        self.assertTrue(accept["accepted"], accept)
        rc, record = self.run_runner("record", *common)
        self.assertEqual(rc, 0, record)
        self.assertTrue(record["recorded"], record)


if __name__ == "__main__":
    unittest.main()
