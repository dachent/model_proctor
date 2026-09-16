"""Behavioral contract for the standalone Codex model-proctor skill."""
from __future__ import annotations

import json
import os
import base64
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
HELPER = SKILL_DIR / "scripts" / "dispatch.py"
SKILL = SKILL_DIR / "SKILL.md"


class StandaloneSkillContract(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="codex-model-proctor-skill-"))
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.adapter_dir = self.tmp / "adapter"
        self.adapter_dir.mkdir()
        self.config = self.adapter_dir / "local-config.json"
        self.config.write_text('{"schema":1,"presets":{}}', encoding="utf-8")
        self.capture = self.tmp / "capture.json"
        self.fake_codex = self.tmp / "desktop-codex.exe"
        self.fake_codex.write_text("placeholder", encoding="utf-8")
        self.adapter = self.adapter_dir / "delegate.py"
        self.adapter.write_text(
            """import json, os, sys
from pathlib import Path
args = sys.argv[1:]
task = sys.stdin.buffer.read().decode('utf-8')
Path(os.environ['MODEL_PROCTOR_CAPTURE']).write_text(json.dumps({
    'args': args, 'task': task
}), encoding='utf-8')
print(json.dumps({'status': 'completed', 'agent_message': 'worker answer'}))
""",
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_helper(self, *extra, text="inspect this repository", codex_executable=True, environment=None,
                   raw_utf8=False, task_base64_stdin=False):
        env = dict(os.environ, MODEL_PROCTOR_CAPTURE=str(self.capture))
        if environment:
            env.update(environment)
        command = [sys.executable, str(HELPER), "--preset", "terra", "--workspace", str(self.workspace),
                   "--adapter-dir", str(self.adapter_dir), "--config", str(self.config)]
        if codex_executable:
            command.extend(["--codex-executable", str(self.fake_codex)])
        if task_base64_stdin:
            command.append("--task-base64-stdin")
        input_text = (base64.b64encode(text.encode("utf-8")).decode("ascii")
                      if task_base64_stdin else text)
        kwargs = {"input": input_text.encode("utf-8") if raw_utf8 else input_text,
                  "capture_output": True, "env": env}
        if not raw_utf8:
            kwargs["text"] = True
        return subprocess.run([*command, *extra], **kwargs)

    def test_skill_is_discoverable_by_its_explicit_name(self):
        self.assertTrue(SKILL.is_file(), "standalone Codex skill is absent")
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("name: model-proctor", text)
        self.assertIn("Use when", text)
        self.assertIn("Luna", text)
        self.assertIn("Terra", text)
        self.assertIn("Astra", text)
        self.assertIn("& python ", text)
        self.assertNotIn("python.exe", text)
        self.assertIn("--task-base64-stdin", text)
        self.assertNotIn("$task = @'", text)
        self.assertIn("straight to the adapter's standard input", text)
        self.assertNotIn("temporary sibling", text)

    def test_helper_passes_stdin_without_a_task_file_and_preserves_read_only_default(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(self.capture.read_text(encoding="utf-8"))
        args = captured["args"]
        self.assertEqual(captured["task"], "inspect this repository")
        self.assertNotIn("--write", args)
        self.assertEqual(args[args.index("--preset") + 1], "terra")
        self.assertEqual(args[args.index("--transport") + 1], "app-server")
        self.assertEqual(args[args.index("--workspace") + 1], str(self.workspace.resolve()))
        self.assertEqual(args[args.index("--codex-executable") + 1], str(self.fake_codex.resolve()))
        self.assertIn("--task-stdin", args)
        self.assertNotIn("--task-file", args)

    def test_helper_passes_write_only_when_explicitly_requested(self):
        result = self.run_helper("--write")
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertIn("--write", captured["args"])

    def test_helper_uses_desktop_bundle_without_a_path_cli_fallback(self):
        local_app_data = self.tmp / "local-app-data"
        old = local_app_data / "OpenAI" / "Codex" / "bin" / "old" / "codex.exe"
        latest = local_app_data / "OpenAI" / "Codex" / "bin" / "latest" / "codex.exe"
        old.parent.mkdir(parents=True); latest.parent.mkdir(parents=True)
        old.write_text("old", encoding="utf-8"); latest.write_text("latest", encoding="utf-8")
        os.utime(old, (1, 1)); os.utime(latest, (2, 2))
        result = self.run_helper(codex_executable=False, environment={
            "LOCALAPPDATA": str(local_app_data), "PATH": str(self.tmp / "not-a-codex-cli"),
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(self.capture.read_text(encoding="utf-8"))
        args = captured["args"]
        self.assertEqual(args[args.index("--codex-executable") + 1], str(latest.resolve()))

    def test_helper_preserves_unicode_and_shell_like_task_text(self):
        task = 'Inspect "quoted" $() `backtick` and \u6f22\u5b57.\nDo not execute the punctuation.'
        result = self.run_helper(text=task, raw_utf8=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertEqual(captured["task"], task)

    def test_helper_preserves_a_here_string_terminator_via_base64_stdin(self):
        task = "Review this PowerShell snippet:\n@'\nsample\n'@\n"
        result = self.run_helper(text=task, task_base64_stdin=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertEqual(captured["task"], task)

    def test_documented_powershell_handoff_preserves_large_here_string_terminator_task(self):
        """The documented pipe avoids both here-string parsing and python.bat argv limits."""
        task = "Review this PowerShell snippet:\n@'\nsample\n'@\n漢字 \"quoted\" $() `backtick`\n" + ("x" * 7030)
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        payload = base64.b64encode(task.encode("utf-8")).decode("ascii")
        script = "\n".join((
            "$dispatcher = " + quote(HELPER),
            "$taskBase64 = " + quote(payload),
            "$taskBase64 | & python $dispatcher --task-base64-stdin --preset terra --workspace " + quote(self.workspace)
            + " --adapter-dir " + quote(self.adapter_dir) + " --config " + quote(self.config)
            + " --codex-executable " + quote(self.fake_codex),
            "exit $LASTEXITCODE",
        ))
        result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True,
                                env=dict(os.environ, MODEL_PROCTOR_CAPTURE=str(self.capture)))
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertEqual(captured["task"], task)


if __name__ == "__main__":
    unittest.main()
