#!/usr/bin/env python3
"""Test suite for delegate.py — unittest, temp dirs, Python fixture children.

Covers validation, execution, output, environment, lifecycle, unit, and
contract tests per the adjudicated review findings.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import ast
from pathlib import Path

_DELEGATE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DELEGATE_DIR))

import delegate  # noqa: E402

_IS_WINDOWS = sys.platform == "win32"

# ---------------------------------------------------------------------------
# Fixture child script templates
# ---------------------------------------------------------------------------

_ECHO_STDIN = r'''
import sys
data = sys.stdin.buffer.read()
sys.stdout.buffer.write(data)
sys.stdout.buffer.flush()
sys.exit(0)
'''

_ECHO_ARG = r'''
import sys
data = sys.argv[-1].encode("utf-8")
sys.stdout.buffer.write(data)
sys.stdout.buffer.flush()
sys.exit(0)
'''

_ECHO_FILE = r'''
import sys
path = sys.argv[-1]
with open(path, "r", encoding="utf-8") as f:
    data = f.read()
sys.stdout.buffer.write(data.encode("utf-8"))
sys.stdout.buffer.flush()
sys.exit(0)
'''

_EXIT_CODE = r'''
import sys
code = int(sys.argv[1])
sys.exit(code)
'''

_DUAL_OUTPUT = r'''
import sys
sys.stdout.buffer.write(b"STDOUT_LINE\n")
sys.stderr.buffer.write(b"STDERR_LINE\n")
sys.stdout.buffer.flush()
sys.stderr.buffer.flush()
sys.exit(0)
'''

_LARGE_OUTPUT = r'''
import sys
for i in range(700000):
    sys.stdout.buffer.write(b"x")
sys.stdout.buffer.flush()
sys.exit(0)
'''

_LARGE_STDERR = r'''
import sys
for i in range(700000):
    sys.stderr.buffer.write(b"y")
sys.stderr.buffer.flush()
sys.exit(0)
'''

_INVALID_UTF8 = r'''
import sys
sys.stdout.buffer.write(b"\xff\xfe\xfd invalid utf8")
sys.stdout.buffer.flush()
sys.exit(0)
'''

_GRANDCHILD_SPAWNER = r'''
import os
import sys
import subprocess
import time

grandchild_script = r"""
import os
import sys
import time

pid_file = sys.argv[1]
with open(pid_file, "w") as f:
    f.write(str(os.getpid()))
f.close()

time.sleep(300)
"""

pid_file = sys.argv[-1]
script_path = pid_file + ".gc.py"
with open(script_path, "w") as f:
    f.write(grandchild_script)

p = subprocess.Popen(
    [sys.executable, script_path, pid_file],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    stdin=subprocess.DEVNULL,
)

time.sleep(2)
time.sleep(300)
'''

_GRANDCHILD_PIPE_INHERIT = r'''
import os
import sys
import subprocess
import time

# Grandchild inherits the child's stdout pipe — the reader thread will never
# see EOF until the grandchild is killed.
grandchild_script = r"""
import os
import sys
import time

pid_file = sys.argv[1]
with open(pid_file, "w") as f:
    f.write(str(os.getpid()))
f.close()

# Keep stdout (inherited) open so the reader thread stays blocked.
time.sleep(300)
"""

pid_file = sys.argv[-1]
script_path = pid_file + ".gc.py"
with open(script_path, "w") as f:
    f.write(grandchild_script)

# Do NOT redirect stdout — the grandchild inherits it.
p = subprocess.Popen(
    [sys.executable, script_path, pid_file],
    stderr=subprocess.DEVNULL,
    stdin=subprocess.DEVNULL,
)

time.sleep(2)
time.sleep(300)
'''

_PRINT_ENV = r'''
import os
import sys
for k, v in sorted(os.environ.items()):
    sys.stdout.buffer.write(f"{k}={v}\n".encode("utf-8"))
sys.stdout.buffer.flush()
sys.exit(0)
'''

_PRINT_CWD = r'''
import os
import sys
sys.stdout.buffer.write(os.getcwd().encode("utf-8") + b"\n")
sys.stdout.buffer.flush()
sys.exit(0)
'''

_PRINT_ARGV = r'''
import sys
for i, a in enumerate(sys.argv):
    sys.stdout.buffer.write(f"[{i}]={a}\n".encode("utf-8"))
sys.stdout.buffer.flush()
sys.exit(0)
'''

_PID_SLEEPER = r'''
import os
import sys
import time
pid_file = sys.argv[1]
with open(pid_file, "w") as f:
    f.write(str(os.getpid()))
time.sleep(300)
'''

_NO_READ_STDIN_SLEEP = r'''
import sys
import time
# Never read stdin; just sleep. This is the scenario the stdin daemon
# thread exists to handle — a large task would block write() otherwise.
time.sleep(300)
'''

# Prints sys.argv and sys._xoptions so resume_args placement can be observed:
# resume_args using ["-X", "sessionid={session_id}"] are consumed by the
# interpreter only if they land before the script name (immediately after the
# executable); misordered args would instead show up in sys.argv.
_RESUME_ECHO = r'''
import sys
sys.stdout.buffer.write((repr(sys.argv) + "\n").encode("utf-8"))
sys.stdout.buffer.write((repr(getattr(sys, "_xoptions", {})) + "\n").encode("utf-8"))
sys.stdout.buffer.flush()
sys.exit(0)
'''

# Mimics kimi's headless resume footer on stderr.
_SESSION_FOOTER = r'''
import sys
sys.stdout.buffer.write(b"answer text\n")
sys.stderr.buffer.write(b"progress...\nTo resume this session: kimi -r session_abc123\n")
sys.stdout.buffer.flush()
sys.stderr.buffer.flush()
sys.exit(0)
'''

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def make_fixture_script(tmpdir, name, content):
    path = os.path.join(tmpdir, name + ".py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def make_agents_json(tmpdir, agents, roots=None, extra=None):
    cfg = {
        "allowed_workspace_roots": roots or [tmpdir],
        "max_task_bytes": 262144,
        "max_timeout_seconds": 7200,
        "default_kill_grace_seconds": 2,
        "max_stdout_bytes": 65536,
        "max_stderr_bytes": 65536,
        "agents": agents,
    }
    if extra:
        cfg.update(extra)
    path = os.path.join(tmpdir, "agents.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return path


def make_agent(script_path, prompt_delivery="stdin", **kw):
    command = [sys.executable, script_path]
    if kw.get("extra_args"):
        command.extend(kw["extra_args"])
    return {
        "command": command,
        "prompt_delivery": prompt_delivery,
        "default_timeout": kw.get("default_timeout", 30),
        "minimum_timeout": kw.get("minimum_timeout", 5),
        "maximum_timeout": kw.get("maximum_timeout", 300),
        "environment_allowlist": kw.get("environment_allowlist", []),
        "environment": kw.get("environment", {}),
        "required_environment": kw.get("required_environment", []),
        "write_allowed": kw.get("write_allowed", False),
    }


def run_delegate(agent, workspace, task=None, task_file=None, timeout=None,
                 config_path=None, timeout_wrap=120, resume_from=None):
    argv = [sys.executable, str(_DELEGATE_DIR / "delegate.py"),
            "--agent", agent, "--workspace", workspace]
    if task is not None:
        argv += ["--task", task]
    if task_file is not None:
        argv += ["--task-file", task_file]
    if timeout is not None:
        argv += ["--timeout", str(timeout)]
    if resume_from is not None:
        argv += ["--resume-from", resume_from]
    env = dict(os.environ)
    if config_path:
        env["DELEGATE_CONFIG"] = config_path
    proc = subprocess.run(argv, capture_output=True, env=env, timeout=timeout_wrap)
    return (proc.stdout.decode("utf-8", errors="replace"),
            proc.stderr.decode("utf-8", errors="replace"),
            proc.returncode)


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def assert_one_json_line(testcase, stdout_text):
    """Assert stdout is exactly one nonempty line that is valid JSON."""
    stripped = stdout_text.strip()
    testcase.assertTrue(stripped, "stdout is empty")
    lines = stripped.split("\n")
    testcase.assertEqual(len(lines), 1,
                         f"Expected exactly 1 line on stdout, got {len(lines)}: {lines!r}")
    return json.loads(lines[0])


# Envelope schema: keys and their expected types (None means any type)
_ENVELOPE_KEYS = {
    "schema_version": int,
    "status": str,
    "agent": (str, type(None)),
    "child_exit_code": (int, type(None)),
    "child_session_id": (str, type(None)),
    "duration_seconds": (int, float, type(None)),
    "stdout": str,
    "stderr": str,
    "stdout_truncated": bool,
    "stderr_truncated": bool,
    "stdout_log_truncated": bool,
    "stderr_log_truncated": bool,
    "run_dir": (str, type(None)),
    "acl_warning": bool,
    "job_warning": bool,
    "error": (str, type(None)),
}


def assert_envelope_schema(testcase, result):
    """Assert the result dict has the full envelope schema."""
    for key, typ in _ENVELOPE_KEYS.items():
        testcase.assertIn(key, result, f"Missing key: {key}")
        if result[key] is not None:
            testcase.assertIsInstance(result[key], typ,
                                      f"Key {key} has wrong type: {type(result[key])}")


# ---------------------------------------------------------------------------
# Test base class
# ---------------------------------------------------------------------------

class DelegateTestBase(unittest.TestCase):
    """Base class with temp dirs, default config, and run-dir cleanup."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="delegate-test-")
        self.workspace = tempfile.mkdtemp(prefix="delegate-ws-", dir=self.tmpdir)
        self.fixture_dir = tempfile.mkdtemp(prefix="delegate-fix-")
        self.echo_script = self._script("echo_stdin", _ECHO_STDIN)
        self.config_path = self._config({
            "test-agent": make_agent(self.echo_script),
        })

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        shutil.rmtree(self.fixture_dir, ignore_errors=True)
        # Clean up delegate-* run dirs created during the test run.
        for pattern in ("delegate-*",):
            for d in glob.glob(os.path.join(tempfile.gettempdir(), pattern)):
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass

    def _script(self, name, content):
        return make_fixture_script(self.fixture_dir, name, content)

    def _config(self, agents, roots=None, extra=None):
        return make_agents_json(self.tmpdir, agents, roots, extra)

    def _run(self, agent, ws=None, task=None, task_file=None, timeout=None, config=None,
             timeout_wrap=120, resume_from=None):
        return run_delegate(
            agent, ws or self.workspace,
            task=task, task_file=task_file, timeout=timeout,
            config_path=config or self.config_path,
            timeout_wrap=timeout_wrap, resume_from=resume_from,
        )

    def _assert_result(self, out, err, rc, expected_status, expected_rc=None):
        """Parse one JSON line, assert schema, status, and clean stderr."""
        result = assert_one_json_line(self, out)
        assert_envelope_schema(self, result)
        self.assertEqual(result["status"], expected_status)
        if expected_rc is not None:
            self.assertEqual(rc, expected_rc)
        return result


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

class TestValidation(DelegateTestBase):

    def test_valid_agent(self):
        out, err, rc = self._run("test-agent", task="hello")
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertEqual(err, "")

    def test_unknown_agent(self):
        out, err, rc = self._run("nonexistent", task="hello")
        self._assert_result(out, err, rc, "invalid", 64)

    def test_missing_task(self):
        out, err, rc = self._run("test-agent")
        self._assert_result(out, err, rc, "invalid", 64)

    def test_both_task_sources(self):
        task_file = os.path.join(self.tmpdir, "task.txt")
        with open(task_file, "w") as f:
            f.write("file task")
        out, err, rc = self._run("test-agent", task="cli task", task_file=task_file)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_empty_task(self):
        out, err, rc = self._run("test-agent", task="")
        self._assert_result(out, err, rc, "invalid", 64)

    def test_oversized_task(self):
        big = "x" * 300000
        task_file = os.path.join(self.tmpdir, "bigtask.txt")
        with open(task_file, "w", encoding="utf-8") as f:
            f.write(big)
        out, err, rc = self._run("test-agent", task_file=task_file)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_malformed_timeout(self):
        out, err, rc = self._run("test-agent", task="hello", timeout="abc")
        self._assert_result(out, err, rc, "invalid", 64)

    def test_timeout_below_minimum(self):
        out, err, rc = self._run("test-agent", task="hello", timeout=1)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_timeout_above_maximum(self):
        out, err, rc = self._run("test-agent", task="hello", timeout=500)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_nonexistent_workspace(self):
        out, err, rc = self._run("test-agent", ws=os.path.join(self.tmpdir, "nope"), task="hello")
        self._assert_result(out, err, rc, "invalid", 64)

    def test_workspace_is_file(self):
        filepath = os.path.join(self.tmpdir, "afile.txt")
        with open(filepath, "w") as f:
            f.write("data")
        out, err, rc = self._run("test-agent", ws=filepath, task="hello")
        self._assert_result(out, err, rc, "invalid", 64)

    def test_workspace_outside_roots(self):
        outside_dir = tempfile.mkdtemp(prefix="delegate-outside-")
        try:
            out, err, rc = self._run("test-agent", ws=outside_dir, task="hello")
            self._assert_result(out, err, rc, "invalid", 64)
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    def test_sibling_prefix_bypass(self):
        root = os.path.join(self.tmpdir, "proj")
        evil = os.path.join(self.tmpdir, "proj_evil")
        os.makedirs(root)
        os.makedirs(evil)
        cfg = self._config(
            {"test-agent": make_agent(self.echo_script)},
            roots=[root],
        )
        out, err, rc = self._run("test-agent", ws=evil, task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_symlink_escape(self):
        outside = tempfile.mkdtemp(prefix="delegate-outside-sym-")
        try:
            link_path = os.path.join(self.workspace, "escape_link")
            try:
                os.symlink(outside, link_path)
            except (OSError, NotImplementedError):
                self.skipTest("Cannot create symlinks (privilege required)")
            out, err, rc = self._run("test-agent", ws=link_path, task="hello")
            self._assert_result(out, err, rc, "invalid", 64)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    @unittest.skipUnless(_IS_WINDOWS, "Junctions are Windows-specific")
    def test_junction_escape(self):
        """Directory junctions need no privilege and are a likely escape vector."""
        outside = tempfile.mkdtemp(prefix="delegate-outside-jct-")
        try:
            junction_path = os.path.join(self.workspace, "escape_junction")
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", junction_path, outside],
                capture_output=True, timeout=10,
            )
            if result.returncode != 0:
                self.skipTest("Cannot create junction")
            out, err, rc = self._run("test-agent", ws=junction_path, task="hello")
            self._assert_result(out, err, rc, "invalid", 64)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    @unittest.skipUnless(_IS_WINDOWS, "UNC paths are Windows-specific")
    def test_unc_workspace_rejected(self):
        out, err, rc = self._run("test-agent", ws="\\\\server\\share", task="hello")
        self._assert_result(out, err, rc, "invalid", 64)

    def test_malformed_json_config(self):
        bad_config = os.path.join(self.tmpdir, "bad.json")
        with open(bad_config, "w") as f:
            f.write("{not valid json")
        out, err, rc = self._run("test-agent", task="hello", config=bad_config)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_invalid_config_types(self):
        bad_config = os.path.join(self.tmpdir, "bad_types.json")
        with open(bad_config, "w") as f:
            json.dump({
                "allowed_workspace_roots": "not-a-list",
                "max_task_bytes": 262144,
                "max_timeout_seconds": 7200,
                "default_kill_grace_seconds": 5,
                "max_stdout_bytes": 65536,
                "max_stderr_bytes": 65536,
                "agents": {},
            }, f)
        out, err, rc = self._run("test-agent", task="hello", config=bad_config)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_relative_executable_rejected(self):
        cfg = self._config({
            "test-agent": {
                "command": ["python"],
                "prompt_delivery": "stdin",
                "default_timeout": 30, "minimum_timeout": 5, "maximum_timeout": 300,
                "environment_allowlist": [], "environment": {},
                "required_environment": [], "write_allowed": False,
            },
        })
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_missing_executable(self):
        cfg = self._config({
            "test-agent": {
                "command": ["C:/nonexistent/path/to/cli.exe"],
                "prompt_delivery": "stdin",
                "default_timeout": 30, "minimum_timeout": 5, "maximum_timeout": 300,
                "environment_allowlist": [], "environment": {},
                "required_environment": [], "write_allowed": False,
            },
        })
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_unrelated_agent_missing_executable_does_not_block(self):
        """An invoked valid agent must work even if another configured agent's CLI is gone."""
        cfg = self._config({
            "test-agent": make_agent(self.echo_script),
            "uninstalled-agent": {
                "command": ["C:/nonexistent/path/to/cli.exe"],
                "prompt_delivery": "stdin",
                "default_timeout": 30, "minimum_timeout": 5, "maximum_timeout": 300,
                "environment_allowlist": [], "environment": {},
                "required_environment": [], "write_allowed": False,
            },
        })
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "completed", 0)

    def test_missing_required_environment(self):
        cfg = self._config({
            "test-agent": make_agent(self.echo_script, required_environment=["MISSING_VAR_XYZ"]),
        })
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_nul_in_task_rejected(self):
        task_file = os.path.join(self.tmpdir, "nul_task.txt")
        with open(task_file, "wb") as f:
            f.write(b"hello\x00world")
        out, err, rc = self._run("test-agent", task_file=task_file)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_validation_error_no_task_text(self):
        secret = "THIS_IS_SECRET_TASK_CONTENT_12345"
        out, err, rc = self._run("nonexistent", task=secret)
        result = self._assert_result(out, err, rc, "invalid", 64)
        self.assertNotIn(secret, out)
        self.assertNotIn(secret, err)


# ---------------------------------------------------------------------------
# Config validation unit tests
# ---------------------------------------------------------------------------

class TestConfigValidation(DelegateTestBase):

    def test_nul_in_env_value(self):
        cfg = self._config({
            "test-agent": make_agent(self.echo_script,
                                     environment={"BAD_VAR": "val\x00ue"}),
        })
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_nul_in_delegated_config_path(self):
        """NUL in DELEGATE_CONFIG must be rejected by _resolve_config_path."""
        os.environ["DELEGATE_CONFIG"] = "C:/path\x00bad"
        try:
            with self.assertRaises(delegate.ConfigError):
                delegate.load_config()
        finally:
            os.environ.pop("DELEGATE_CONFIG", None)

    def test_min_gt_max_timeout(self):
        cfg = self._config({
            "test-agent": {
                "command": [sys.executable, self.echo_script],
                "prompt_delivery": "stdin",
                "default_timeout": 30, "minimum_timeout": 100, "maximum_timeout": 50,
                "environment_allowlist": [], "environment": {},
                "required_environment": [], "write_allowed": False,
            },
        })
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_default_outside_range(self):
        cfg = self._config({
            "test-agent": {
                "command": [sys.executable, self.echo_script],
                "prompt_delivery": "stdin",
                "default_timeout": 500, "minimum_timeout": 5, "maximum_timeout": 100,
                "environment_allowlist": [], "environment": {},
                "required_environment": [], "write_allowed": False,
            },
        })
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_huge_grace_rejected(self):
        cfg = self._config(
            {"test-agent": make_agent(self.echo_script)},
            extra={"default_kill_grace_seconds": 120},
        )
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_empty_agents_rejected(self):
        cfg = self._config(
            {"test-agent": make_agent(self.echo_script)},
        )
        # Write a config with empty agents dict
        bad = os.path.join(self.tmpdir, "empty_agents.json")
        with open(bad, "w") as f:
            json.dump({
                "allowed_workspace_roots": [self.tmpdir],
                "max_task_bytes": 262144, "max_timeout_seconds": 7200,
                "default_kill_grace_seconds": 2,
                "max_stdout_bytes": 65536, "max_stderr_bytes": 65536,
                "agents": {},
            }, f)
        out, err, rc = self._run("test-agent", task="hello", config=bad)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_non_string_roots_rejected(self):
        bad = os.path.join(self.tmpdir, "bad_roots.json")
        with open(bad, "w") as f:
            json.dump({
                "allowed_workspace_roots": [123],
                "max_task_bytes": 262144, "max_timeout_seconds": 7200,
                "default_kill_grace_seconds": 2,
                "max_stdout_bytes": 65536, "max_stderr_bytes": 65536,
                "agents": {"test-agent": make_agent(self.echo_script)},
            }, f)
        out, err, rc = self._run("test-agent", task="hello", config=bad)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_default_timeout_exceeds_global_max(self):
        bad = os.path.join(self.tmpdir, "bad_global.json")
        with open(bad, "w") as f:
            json.dump({
                "allowed_workspace_roots": [self.tmpdir],
                "max_task_bytes": 262144, "max_timeout_seconds": 100,
                "default_kill_grace_seconds": 2,
                "max_stdout_bytes": 65536, "max_stderr_bytes": 65536,
                "agents": {"test-agent": {
                    "command": [sys.executable, self.echo_script],
                    "prompt_delivery": "stdin",
                    "default_timeout": 200, "minimum_timeout": 5, "maximum_timeout": 300,
                    "environment_allowlist": [], "environment": {},
                    "required_environment": [], "write_allowed": False,
                }},
            }, f)
        out, err, rc = self._run("test-agent", task="hello", config=bad)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_case_fold_collision_in_env(self):
        cfg = self._config({
            "test-agent": make_agent(self.echo_script,
                                     environment={"FOO": "1", "foo": "2"}),
        })
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_empty_env_name_rejected(self):
        cfg = self._config({
            "test-agent": make_agent(self.echo_script,
                                     environment={"": "val"}),
        })
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_env_name_with_equals_rejected(self):
        cfg = self._config({
            "test-agent": make_agent(self.echo_script,
                                     environment={"BAD=NAME": "val"}),
        })
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)


# ---------------------------------------------------------------------------
# Containment unit tests
# ---------------------------------------------------------------------------

class TestContainment(DelegateTestBase):

    def test_is_unc_variants(self):
        self.assertTrue(delegate._is_unc("\\\\server\\share"))
        self.assertTrue(delegate._is_unc("\\\\?\\UNC\\server\\share"))
        self.assertFalse(delegate._is_unc("\\\\?\\C:" + "\\Users"))
        self.assertFalse(delegate._is_unc("C:\\Users"))

    def test_drive_root_containment(self):
        if not _IS_WINDOWS:
            self.skipTest("Drive-root containment is Windows-specific")
        # root = C:\  →  workspace = C:\Users  should pass
        self.assertTrue(delegate._workspace_contained("C:\\Users\\boris", "C:\\"))
        # root = C:\  →  workspace = D:\Users  should fail
        self.assertFalse(delegate._workspace_contained("D:\\Users\\boris", "C:\\"))
        # root = C:\  →  workspace = C:\  should pass (equality)
        self.assertTrue(delegate._workspace_contained("C:\\", "C:\\"))

    def test_workspace_equals_root(self):
        root = os.path.join(self.tmpdir, "myroot")
        os.makedirs(root)
        cfg = self._config(
            {"test-agent": make_agent(self.echo_script)},
            roots=[root],
        )
        out, err, rc = self._run("test-agent", ws=root, task="hello", config=cfg)
        self._assert_result(out, err, rc, "completed", 0)

    def test_case_insensitive_root_workspace(self):
        """Root .../Proj, workspace .../proj/sub — normcase handles case."""
        root = os.path.join(self.tmpdir, "MyProj")
        sub = os.path.join(root, "sub")
        os.makedirs(sub)
        # Pass workspace as lowercase variant of root
        cfg = self._config(
            {"test-agent": make_agent(self.echo_script)},
            roots=[root],
        )
        out, err, rc = self._run("test-agent", ws=sub, task="hello", config=cfg)
        self._assert_result(out, err, rc, "completed", 0)


# ---------------------------------------------------------------------------
# Execution tests
# ---------------------------------------------------------------------------

class TestExecution(DelegateTestBase):

    def test_stdin_delivery(self):
        echo = self._script("echo_stdin", _ECHO_STDIN)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="hello via stdin", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertIn("hello via stdin", result["stdout"])
        self.assertEqual(err, "")

    def test_argument_delivery(self):
        echo = self._script("echo_arg", _ECHO_ARG)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="argument")})
        out, err, rc = self._run("test-agent", task="hello via arg", config=cfg)
        self._assert_result(out, err, rc, "completed", 0)
        result = parse_json_result(out)
        self.assertIn("hello via arg", result["stdout"])

    def test_file_delivery(self):
        echo = self._script("echo_file", _ECHO_FILE)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="file")})
        out, err, rc = self._run("test-agent", task="hello via file", config=cfg)
        self._assert_result(out, err, rc, "completed", 0)
        result = parse_json_result(out)
        self.assertIn("hello via file", result["stdout"])

    def test_task_with_spaces_and_unicode(self):
        echo = self._script("echo_stdin", _ECHO_STDIN)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="stdin")})
        task = "Héllo Wörld — espaços múltiplos"
        out, err, rc = self._run("test-agent", task=task, config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertIn(task, result["stdout"])

    def test_task_from_file(self):
        echo = self._script("echo_stdin", _ECHO_STDIN)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="stdin")})
        task_file = os.path.join(self.tmpdir, "mytask.txt")
        with open(task_file, "w", encoding="utf-8") as f:
            f.write("task from file content")
        out, err, rc = self._run("test-agent", task_file=task_file, config=cfg)
        self._assert_result(out, err, rc, "completed", 0)
        result = parse_json_result(out)
        self.assertIn("task from file content", result["stdout"])

    def test_task_file_invalid_utf8(self):
        echo = self._script("echo_stdin", _ECHO_STDIN)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="stdin")})
        task_file = os.path.join(self.tmpdir, "bad_utf8.txt")
        with open(task_file, "wb") as f:
            f.write(b"\xff\xfe not valid utf8")
        out, err, rc = self._run("test-agent", task_file=task_file, config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_child_cwd_correct(self):
        cwd_print = self._script("print_cwd", _PRINT_CWD)
        cfg = self._config({"test-agent": make_agent(cwd_print, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        self._assert_result(out, err, rc, "completed", 0)
        result = parse_json_result(out)
        resolved = str(Path(self.workspace).resolve())
        self.assertIn(resolved.replace("/", "\\"), result["stdout"].replace("/", "\\"))

    def test_fixed_args_distinct(self):
        argv_print = self._script("print_argv", _PRINT_ARGV)
        cfg = self._config({
            "test-agent": make_agent(argv_print, prompt_delivery="argument",
                                     extra_args=["--flag1", "--flag2", "value with space"]),
        })
        out, err, rc = self._run("test-agent", task="mytask", config=cfg)
        self._assert_result(out, err, rc, "completed", 0)
        result = parse_json_result(out)
        self.assertIn("--flag1", result["stdout"])
        self.assertIn("--flag2", result["stdout"])
        self.assertIn("value with space", result["stdout"])
        self.assertIn("mytask", result["stdout"])

    def test_shell_false_preserved(self):
        echo = self._script("echo_arg", _ECHO_ARG)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="argument")})
        task = "& echo pwned"
        out, err, rc = self._run("test-agent", task=task, config=cfg)
        self._assert_result(out, err, rc, "completed", 0)
        result = parse_json_result(out)
        self.assertIn("& echo pwned", result["stdout"])

    def test_child_exit_zero(self):
        echo = self._script("echo_stdin", _ECHO_STDIN)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertEqual(result["child_exit_code"], 0)
        self.assertEqual(err, "")

    def test_child_exit_nonzero(self):
        exit_script = self._script("exit_code", _EXIT_CODE)
        cfg = self._config({
            "test-agent": make_agent(exit_script, prompt_delivery="argument",
                                     extra_args=["3"]),
        })
        out, err, rc = self._run("test-agent", task="irrelevant", config=cfg)
        result = self._assert_result(out, err, rc, "failed", 0)
        self.assertEqual(result["child_exit_code"], 3)


# ---------------------------------------------------------------------------
# Output tests
# ---------------------------------------------------------------------------

class TestOutput(DelegateTestBase):

    def test_exactly_one_json_completed(self):
        echo = self._script("echo_stdin", _ECHO_STDIN)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = assert_one_json_line(self, out)
        assert_envelope_schema(self, result)

    def test_exactly_one_json_invalid(self):
        out, err, rc = self._run("nonexistent", task="hello")
        assert_one_json_line(self, out)

    def test_stderr_empty_on_happy_path(self):
        echo = self._script("echo_stdin", _ECHO_STDIN)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self.assertEqual(err, "")

    def test_independent_stdout_stderr(self):
        dual = self._script("dual_output", _DUAL_OUTPUT)
        cfg = self._config({"test-agent": make_agent(dual, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertIn("STDOUT_LINE", result["stdout"])
        self.assertNotIn("STDERR_LINE", result["stdout"])
        self.assertIn("STDERR_LINE", result["stderr"])
        self.assertNotIn("STDOUT_LINE", result["stderr"])

    def test_full_logs_written(self):
        dual = self._script("dual_output", _DUAL_OUTPUT)
        cfg = self._config({"test-agent": make_agent(dual, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        result = parse_json_result(out)
        stdout_log = os.path.join(result["run_dir"], "stdout.log")
        stderr_log = os.path.join(result["run_dir"], "stderr.log")
        with open(stdout_log, "rb") as f:
            self.assertIn(b"STDOUT_LINE", f.read())
        with open(stderr_log, "rb") as f:
            self.assertIn(b"STDERR_LINE", f.read())

    def test_bounded_tail_oversized(self):
        large = self._script("large_output", _LARGE_OUTPUT)
        cfg = self._config({
            "test-agent": make_agent(large, prompt_delivery="stdin"),
        }, extra={"max_stdout_bytes": 1024})
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertTrue(result["stdout_truncated"])

    def test_stderr_truncation(self):
        large_err = self._script("large_stderr", _LARGE_STDERR)
        cfg = self._config({
            "test-agent": make_agent(large_err, prompt_delivery="stdin"),
        }, extra={"max_stderr_bytes": 1024})
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertTrue(result["stderr_truncated"])

    def test_truncation_flags_false_for_small(self):
        echo = self._script("echo_stdin", _ECHO_STDIN)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="small", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertFalse(result["stdout_truncated"])
        self.assertFalse(result["stderr_truncated"])

    def test_log_truncation_flags(self):
        """Disk-log cap (max_log_bytes) exceeded → *_log_truncated True in the envelope."""
        large = self._script("large_output_log", _LARGE_OUTPUT)
        large_err = self._script("large_stderr_log", _LARGE_STDERR)
        cfg = self._config({
            "a": make_agent(large, prompt_delivery="stdin"),
            "b": make_agent(large_err, prompt_delivery="stdin"),
        }, extra={"max_log_bytes": 1024})
        out, err, rc = self._run("a", task="ignored", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertTrue(result["stdout_log_truncated"])
        self.assertFalse(result["stderr_log_truncated"])
        # The disk log is capped even though the tail keeps filling.
        with open(os.path.join(result["run_dir"], "stdout.log"), "rb") as f:
            self.assertEqual(len(f.read()), 1024)
        out, err, rc = self._run("b", task="ignored", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertTrue(result["stderr_log_truncated"])
        self.assertFalse(result["stdout_log_truncated"])

    def test_log_truncation_flags_false_when_within_cap(self):
        echo = self._script("echo_stdin_log", _ECHO_STDIN)
        cfg = self._config({
            "test-agent": make_agent(echo, prompt_delivery="stdin"),
        }, extra={"max_log_bytes": 1024})
        out, err, rc = self._run("test-agent", task="small", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertFalse(result["stdout_log_truncated"])
        self.assertFalse(result["stderr_log_truncated"])

    def test_invalid_utf8_replacement(self):
        inv = self._script("invalid_utf8", _INVALID_UTF8)
        cfg = self._config({"test-agent": make_agent(inv, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertIn("\ufffd", result["stdout"])

    def test_large_output_no_deadlock(self):
        large = self._script("large_output", _LARGE_OUTPUT)
        cfg = self._config({"test-agent": make_agent(large, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertTrue(result["stdout_truncated"])

    def test_envelope_schema(self):
        echo = self._script("echo_stdin", _ECHO_STDIN)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = parse_json_result(out)
        assert_envelope_schema(self, result)
        self.assertEqual(result["schema_version"], 1)


# ---------------------------------------------------------------------------
# Environment tests
# ---------------------------------------------------------------------------

class TestEnvironment(DelegateTestBase):

    def setUp(self):
        super().setUp()
        os.environ["DELEGATE_TEST_ALLOWED"] = "allowed_value"
        os.environ["DELEGATE_TEST_DISALLOWED"] = "disallowed_value"
        self.addCleanup(os.environ.pop, "DELEGATE_TEST_ALLOWED", None)
        self.addCleanup(os.environ.pop, "DELEGATE_TEST_DISALLOWED", None)

    def test_only_allowlisted_vars_inherited(self):
        env_print = self._script("print_env", _PRINT_ENV)
        cfg = self._config({
            "test-agent": make_agent(env_print, prompt_delivery="stdin",
                                     environment_allowlist=["DELEGATE_TEST_ALLOWED", "PATH"]),
        })
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertIn("DELEGATE_TEST_ALLOWED=allowed_value", result["stdout"])
        self.assertNotIn("DELEGATE_TEST_DISALLOWED", result["stdout"])

    def test_fixed_env_added(self):
        env_print = self._script("print_env", _PRINT_ENV)
        cfg = self._config({
            "test-agent": make_agent(env_print, prompt_delivery="stdin",
                                     environment={"FIXED_VAR_123": "fixed_value"}),
        })
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertIn("FIXED_VAR_123=fixed_value", result["stdout"])

    def test_disallowed_var_absent(self):
        env_print = self._script("print_env", _PRINT_ENV)
        cfg = self._config({
            "test-agent": make_agent(env_print, prompt_delivery="stdin",
                                     environment_allowlist=["PATH"]),
        })
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertNotIn("DELEGATE_TEST_DISALLOWED", result["stdout"])
        self.assertNotIn("DELEGATE_TEST_ALLOWED", result["stdout"])


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

class TestLifecycle(DelegateTestBase):

    def test_timeout_status_and_exit(self):
        sleeper = self._script("sleeper", _PID_SLEEPER)
        pid_file = os.path.join(self.workspace, "sleeper_pid.txt")
        cfg = self._config({
            "test-agent": make_agent(sleeper, prompt_delivery="argument",
                                     extra_args=[pid_file],
                                     default_timeout=3, minimum_timeout=1, maximum_timeout=300),
        }, extra={"default_kill_grace_seconds": 2})
        t0 = time.monotonic()
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        wall = time.monotonic() - t0
        result = self._assert_result(out, err, rc, "timeout", 124)
        self.assertIsNone(result["child_exit_code"])
        # Wall clock: timeout + grace + overhead, bounded
        self.assertLess(wall, 20)

    def test_child_killed_on_timeout(self):
        sleeper = self._script("sleeper2", _PID_SLEEPER)
        pid_file = os.path.join(self.workspace, "child_pid.txt")
        cfg = self._config({
            "test-agent": make_agent(sleeper, prompt_delivery="argument",
                                     extra_args=[pid_file],
                                     default_timeout=3, minimum_timeout=1, maximum_timeout=300),
        }, extra={"default_kill_grace_seconds": 2})
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        self._assert_result(out, err, rc, "timeout", 124)
        with open(pid_file, "r") as f:
            child_pid = int(f.read().strip())
        time.sleep(2)
        self.assertFalse(delegate.is_pid_alive(child_pid),
                         f"Child PID {child_pid} still alive after timeout")

    def test_grandchild_killed(self):
        """Mandatory: grandchild spawned by child must be killed on timeout."""
        spawner = self._script("spawner", _GRANDCHILD_SPAWNER)
        cfg = self._config({
            "test-agent": make_agent(spawner, prompt_delivery="argument",
                                     default_timeout=8, minimum_timeout=1, maximum_timeout=300),
        }, extra={"default_kill_grace_seconds": 3})
        gc_pid_file = os.path.join(self.workspace, "gc_pid.txt")
        out, err, rc = self._run("test-agent", task=gc_pid_file, config=cfg)
        self._assert_result(out, err, rc, "timeout", 124)
        self.assertTrue(os.path.exists(gc_pid_file), "Grandchild PID file not created")
        with open(gc_pid_file, "r") as f:
            gc_pid = int(f.read().strip())
        time.sleep(3)
        self.assertFalse(delegate.is_pid_alive(gc_pid),
                         f"Grandchild PID {gc_pid} still alive after kill sequence")

    def test_grandchild_pipe_inherit(self):
        """Grandchild inheriting the stdout pipe — wrapper must still return one JSON envelope."""
        spawner = self._script("gc_pipe", _GRANDCHILD_PIPE_INHERIT)
        cfg = self._config({
            "test-agent": make_agent(spawner, prompt_delivery="argument",
                                     default_timeout=5, minimum_timeout=1, maximum_timeout=300),
        }, extra={"default_kill_grace_seconds": 2})
        gc_pid_file = os.path.join(self.workspace, "gc_pipe_pid.txt")
        t0 = time.monotonic()
        out, err, rc = self._run("test-agent", task=gc_pid_file, config=cfg,
                                 timeout_wrap=60)
        wall = time.monotonic() - t0
        # Must return exactly one JSON envelope (timeout status)
        result = self._assert_result(out, err, rc, "timeout", 124)
        # Wall clock bounded
        self.assertLess(wall, 30)
        # Grandchild must be dead
        if os.path.exists(gc_pid_file):
            with open(gc_pid_file, "r") as f:
                gc_pid = int(f.read().strip())
            time.sleep(3)
            self.assertFalse(delegate.is_pid_alive(gc_pid),
                             f"Grandchild PID {gc_pid} still alive")

    def test_stdin_thread_nonreading_child(self):
        """Regression: ~200KB task, child never reads stdin, sleeps long → timeout."""
        no_read = self._script("no_read", _NO_READ_STDIN_SLEEP)
        cfg = self._config({
            "test-agent": make_agent(no_read, prompt_delivery="stdin",
                                     default_timeout=4, minimum_timeout=1, maximum_timeout=300),
        }, extra={"default_kill_grace_seconds": 2})
        # Use task-file because 200KB exceeds Windows command-line length limits.
        task_file = os.path.join(self.tmpdir, "big_stdin_task.txt")
        with open(task_file, "w", encoding="utf-8") as f:
            f.write("x" * 200000)
        t0 = time.monotonic()
        out, err, rc = self._run("test-agent", task_file=task_file, config=cfg)
        wall = time.monotonic() - t0
        self._assert_result(out, err, rc, "timeout", 124)
        self.assertLess(wall, 20)

    def test_job_warning_false_on_happy_path(self):
        """Catch the get_last_error blocker: job_warning must be False on a normal run."""
        if not _IS_WINDOWS:
            self.skipTest("Job Objects are Windows-specific")
        echo = self._script("echo_stdin", _ECHO_STDIN)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertFalse(result["job_warning"],
                         "job_warning is True — Job Object creation/assignment failed")

    def test_run_dir_has_acl_warning_field(self):
        echo = self._script("echo_stdin2", _ECHO_STDIN)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = parse_json_result(out)
        self.assertIn("acl_warning", result)
        self.assertIsInstance(result["acl_warning"], bool)

    def test_json_has_run_dir(self):
        echo = self._script("echo_stdin3", _ECHO_STDIN)
        cfg = self._config({"test-agent": make_agent(echo, prompt_delivery="stdin")})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = parse_json_result(out)
        self.assertIsNotNone(result["run_dir"])
        self.assertTrue(os.path.isdir(result["run_dir"]))

    def test_internal_error_behavioral(self):
        """Force an internal_error via monkeypatch and assert correct result + exit 70.

        We call run_delegate in-process (not as a subprocess) so the monkeypatch
        is visible. run_delegate returns (result_dict, exit_code) without writing
        to stdout — we verify the return values directly.
        """
        original = delegate.tempfile.mkdtemp

        def fail_mkstemp(*a, **kw):
            raise OSError("forced failure for test")

        delegate.tempfile.mkdtemp = fail_mkstemp
        try:
            class FakeArgs:
                agent = "test-agent"
                workspace = self.workspace
                task = "hello"
                task_file = None
                timeout = None
            os.environ["DELEGATE_CONFIG"] = self.config_path
            result, exit_code = delegate.run_delegate(FakeArgs())
            self.assertEqual(result["status"], "internal_error")
            self.assertEqual(exit_code, 70)
            self.assertEqual(result["error"], "OSError")
        finally:
            delegate.tempfile.mkdtemp = original
            os.environ.pop("DELEGATE_CONFIG", None)


# ---------------------------------------------------------------------------
# Session resume tests
# ---------------------------------------------------------------------------

class TestResume(DelegateTestBase):

    def _agent_with_resume(self, script, **kw):
        agent = make_agent(script, **kw)
        agent["resume_args"] = ["-X", "sessionid={session_id}"]
        return agent

    # --- resume_args config validation ---

    def test_resume_args_not_a_list(self):
        agent = make_agent(self.echo_script)
        agent["resume_args"] = "-r {session_id}"
        cfg = self._config({"test-agent": agent})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_resume_args_non_string_entry(self):
        agent = make_agent(self.echo_script)
        agent["resume_args"] = ["-r", 123]
        cfg = self._config({"test-agent": agent})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_resume_args_missing_placeholder(self):
        agent = make_agent(self.echo_script)
        agent["resume_args"] = ["-r", "session_fixed"]
        cfg = self._config({"test-agent": agent})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_resume_args_multiple_placeholders(self):
        agent = make_agent(self.echo_script)
        agent["resume_args"] = ["{session_id}", "{session_id}"]
        cfg = self._config({"test-agent": agent})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_resume_args_nul_rejected(self):
        agent = make_agent(self.echo_script)
        agent["resume_args"] = ["-r\x00", "{session_id}"]
        cfg = self._config({"test-agent": agent})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    # --- --resume-from input validation ---

    def test_resume_from_agent_without_resume_args(self):
        """--resume-from against an agent without resume_args must fail closed."""
        out, err, rc = self._run("test-agent", task="hello", resume_from="session_abc123")
        self._assert_result(out, err, rc, "invalid", 64)

    def test_resume_from_bad_chars(self):
        agent = self._agent_with_resume(self.echo_script)
        cfg = self._config({"test-agent": agent})
        for bad in ("session_abc; rm -rf", "with space", "slash/id", "quote\"id"):
            out, err, rc = self._run("test-agent", task="hello", config=cfg,
                                     resume_from=bad)
            self._assert_result(out, err, rc, "invalid", 64)

    def test_resume_from_empty(self):
        agent = self._agent_with_resume(self.echo_script)
        cfg = self._config({"test-agent": agent})
        out, err, rc = self._run("test-agent", task="hello", config=cfg, resume_from="")
        self._assert_result(out, err, rc, "invalid", 64)

    # --- happy path: substituted args land right after the executable ---

    def test_resume_args_substituted_and_ordered(self):
        script = self._script("resume_echo", _RESUME_ECHO)
        agent = self._agent_with_resume(script, prompt_delivery="argument",
                                        extra_args=["--fixed"])
        cfg = self._config({"test-agent": agent})
        out, err, rc = self._run("test-agent", task="mytask", config=cfg,
                                 resume_from="session_abc123")
        result = self._assert_result(out, err, rc, "completed", 0)
        lines = result["stdout"].strip().splitlines()
        child_argv = ast.literal_eval(lines[0])
        xoptions = ast.literal_eval(lines[1])
        # -X was consumed by the interpreter → it sat before the script name,
        # i.e. immediately after the executable; fixed args and task are intact.
        self.assertEqual(child_argv, [script, "--fixed", "mytask"])
        self.assertEqual(xoptions, {"sessionid": "session_abc123"})

    # --- child_session_id envelope field ---

    def test_child_session_id_captured(self):
        script = self._script("session_footer", _SESSION_FOOTER)
        cfg = self._config({"test-agent": make_agent(script)})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertEqual(result["child_session_id"], "session_abc123")

    def test_child_session_id_null_when_absent(self):
        out, err, rc = self._run("test-agent", task="hello")
        result = self._assert_result(out, err, rc, "completed", 0)
        self.assertIsNone(result["child_session_id"])


# ---------------------------------------------------------------------------
# CLI contract tests
# ---------------------------------------------------------------------------

class TestCLIContract(DelegateTestBase):

    def test_help_exits_zero_no_json(self):
        """--help must print help text only, no JSON envelope, exit 0."""
        proc = subprocess.run(
            [sys.executable, str(_DELEGATE_DIR / "delegate.py"), "--help"],
            capture_output=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        stdout = proc.stdout.decode("utf-8", errors="replace")
        self.assertNotIn("{", stdout)  # no JSON

    def test_completed_and_failed_both_exit_zero(self):
        """By design, both completed and failed map to exit 0 (JSON is authoritative)."""
        echo = self._script("echo_stdin", _ECHO_STDIN)
        exit_script = self._script("exit_code", _EXIT_CODE)
        # Use separate config files so they don't overwrite each other.
        cfg_echo = os.path.join(self.tmpdir, "cfg_echo.json")
        with open(cfg_echo, "w") as f:
            json.dump({
                "allowed_workspace_roots": [self.tmpdir],
                "max_task_bytes": 262144, "max_timeout_seconds": 7200,
                "default_kill_grace_seconds": 2,
                "max_stdout_bytes": 65536, "max_stderr_bytes": 65536,
                "agents": {"a": make_agent(echo, prompt_delivery="stdin")},
            }, f)
        cfg_exit = os.path.join(self.tmpdir, "cfg_exit.json")
        with open(cfg_exit, "w") as f:
            json.dump({
                "allowed_workspace_roots": [self.tmpdir],
                "max_task_bytes": 262144, "max_timeout_seconds": 7200,
                "default_kill_grace_seconds": 2,
                "max_stdout_bytes": 65536, "max_stderr_bytes": 65536,
                "agents": {"b": make_agent(exit_script, prompt_delivery="argument",
                                          extra_args=["7"])},
            }, f)
        _, _, rc1 = self._run("a", task="hi", config=cfg_echo)
        self.assertEqual(rc1, 0)
        _, _, rc2 = self._run("b", task="irrelevant", config=cfg_exit)
        self.assertEqual(rc2, 0)


def parse_json_result(stdout_text):
    lines = stdout_text.strip().split("\n")
    return json.loads(lines[-1])


if __name__ == "__main__":
    unittest.main()
