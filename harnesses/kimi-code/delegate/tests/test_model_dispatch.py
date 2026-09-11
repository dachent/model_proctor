#!/usr/bin/env python3
"""Model-mode dispatch tests (#96 / TOOL-031).

"start subagent with model [x]": --model dispatch synthesizes the agent
entry from the hardened template, validates the id against kimi's LIVE
config (never a cached copy), refuses production-pattern prompts, refuses
nested/--write misuse, and injects the PROCTOR_CHILD marker. Every test
drives the real CLI with fixture configs — no model calls.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_DELEGATE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DELEGATE_DIR))
import delegate  # noqa: E402

DELEGATE_PY = _DELEGATE_DIR / "delegate.py"

# Child that echoes its first argv token (the interpolated model id), the
# task (last argv), and the PROCTOR_CHILD marker — proving all three
# crossed the boundary as intended.
_ECHO_MODEL = r'''
import os, sys
sys.stdout.write("model=" + sys.argv[1] + "\n")
sys.stdout.write("task=" + sys.argv[-1] + "\n")
sys.stdout.write("marker=" + os.environ.get("PROCTOR_CHILD", "") + "\n")
sys.stdout.flush()
'''


def make_home(tmp, models):
    """A fake kimi KIMI_CODE_HOME with a config.toml listing `models`."""
    home = Path(tmp) / "home"
    home.mkdir()
    lines = ['default_model = "x"\n']
    for m in models:
        lines.append(f'[models."{m}"]\nprovider = "fireworks"\n'
                     f'model = "accounts/fireworks/models/{m}"\n')
    (home / "config.toml").write_text("".join(lines), encoding="utf-8")
    return str(home)


def make_config(tmp, template):
    cfg = {
        "allowed_workspace_roots": [tmp],
        "max_task_bytes": 262144,
        "max_timeout_seconds": 7200,
        "default_kill_grace_seconds": 2,
        "max_stdout_bytes": 65536,
        "max_stderr_bytes": 65536,
        "agents": {"stub": {
            "command": [sys.executable, "-c", "print('unused')"],
            "prompt_delivery": "argument",
            "default_timeout": 30, "minimum_timeout": 5,
            "maximum_timeout": 300, "environment_allowlist": [],
            "environment": {}, "required_environment": [],
            "write_allowed": False,
        }},
    }
    if template is not None:
        cfg["model_dispatch_template"] = template
    p = Path(tmp) / "agents.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return str(p)


def template_for(tmp):
    """A dispatchable template: python -c ECHO_MODEL {model}."""
    script = Path(tmp) / "echo_model.py"
    script.write_text(_ECHO_MODEL, encoding="utf-8")
    return {
        "command": [sys.executable, str(script), "{model}"],
        "prompt_delivery": "argument",
        "default_timeout": 30, "minimum_timeout": 5,
        "maximum_timeout": 300,
        "environment_allowlist": [], "environment": {},
        "required_environment": [], "write_allowed": False,
        "allow_breakaway": False,
    }


def run_cli(argv, config=None, home=None, extra_env=None):
    env = dict(os.environ)
    env.pop("DELEGATE_CONFIG", None)
    env.pop("KIMI_CODE_HOME", None)
    env.pop("PROCTOR_CHILD", None)
    if config:
        env["DELEGATE_CONFIG"] = config
    if home:
        env["KIMI_CODE_HOME"] = home
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([sys.executable, str(DELEGATE_PY), *argv],
                       capture_output=True, text=True, env=env, timeout=120)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


class LiveCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="catalog-unit-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_ids_from_kimi_config(self):
        os.environ["KIMI_CODE_HOME"] = make_home(self.tmp,
                                                 ["fake/alpha", "fake/beta"])
        try:
            ids, note = delegate.live_harness_models()
        finally:
            os.environ.pop("KIMI_CODE_HOME", None)
        self.assertIsNone(note)
        self.assertEqual(ids, {"fake/alpha", "fake/beta"})

    def test_missing_config_is_a_note_not_an_empty_catalog(self):
        os.environ["KIMI_CODE_HOME"] = os.path.join(self.tmp, "no-such-home")
        try:
            ids, note = delegate.live_harness_models()
        finally:
            os.environ.pop("KIMI_CODE_HOME", None)
        self.assertEqual(ids, set())
        self.assertIn("unreadable", note)

    def test_model_family_groups_tier_suffixes(self):
        self.assertEqual(delegate._model_family("fireworks/glm-5p3-flash"),
                         "fireworks/glm-5p3")
        self.assertEqual(delegate._model_family("fireworks/kimi-k3-fast"),
                         "fireworks/kimi-k3")


class ResolveModelAgent(unittest.TestCase):
    def test_interpolates_and_forces_safety_defaults(self):
        cfg = {"model_dispatch_template": {
            "command": ["exe", "-m", "{model}", "-p"],
            "resume_args": ["-r", "{session_id}"],
            "prompt_delivery": "argument",
            "default_timeout": 100, "minimum_timeout": 5,
            "maximum_timeout": 300, "environment_allowlist": [],
            "environment": {}, "required_environment": [],
            "write_allowed": True, "allow_breakaway": True,
        }}
        agent = delegate.resolve_model_agent(cfg, "fake/alpha", write=True)
        self.assertEqual(agent["command"], ["exe", "-m", "fake/alpha", "-p"])
        self.assertTrue(agent["write_allowed"])
        self.assertFalse(agent["allow_breakaway"])  # forced off, always
        agent_ro = delegate.resolve_model_agent(cfg, "fake/alpha", write=False)
        self.assertFalse(agent_ro["write_allowed"])

    def test_missing_template_is_a_config_error(self):
        with self.assertRaises(delegate.ConfigError):
            delegate.resolve_model_agent({}, "fake/alpha", write=False)


class ConfigValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="catalog-cfg-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self, template):
        os.environ["DELEGATE_CONFIG"] = make_config(self.tmp, template)
        try:
            delegate.load_config()
        finally:
            os.environ.pop("DELEGATE_CONFIG", None)

    def test_template_without_model_placeholder_refused(self):
        tpl = template_for(self.tmp)
        tpl["command"] = [sys.executable, tpl["command"][1]]
        with self.assertRaises(delegate.ConfigError) as ctx:
            self._load(tpl)
        self.assertIn("{model}", str(ctx.exception))

    def test_template_with_breakaway_refused(self):
        tpl = template_for(self.tmp)
        tpl["allow_breakaway"] = True
        with self.assertRaises(delegate.ConfigError) as ctx:
            self._load(tpl)
        self.assertIn("allow_breakaway", str(ctx.exception))


class ModelDispatchCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="model-dispatch-")
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(self.ws)
        self.home = make_home(self.tmp, ["fake/alpha", "fake/alpha-flash",
                                         "fireworks/glm-5p3-flash"])
        self.config = make_config(self.tmp, template_for(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, model, task="hello", write=False, extra_env=None,
             config=True, home=True):
        argv = ["--model", model, "--workspace", self.ws, "--task", task]
        if write:
            argv.append("--write")
        return run_cli(argv,
                       config=self.config if config else None,
                       home=self.home if home else None,
                       extra_env=extra_env)

    def test_unknown_model_refused_with_same_family_alternatives(self):
        rc, out = self._run("fake/alpha-pro")
        self.assertEqual(rc, 64)
        self.assertIn("model_not_in_harness_catalog", out["error"])
        self.assertEqual(out["same_family_alternatives"],
                         ["fake/alpha", "fake/alpha-flash"])
        self.assertEqual(out["catalog_size"], 3)

    def test_production_entrypoint_refused(self):
        rc, out = self._run("fake/alpha",
                            task="Run the weekly extract via run_week.ps1 "
                                 "and report.")
        self.assertEqual(rc, 64)
        self.assertIn("production_entrypoint_refused", out["error"])
        self.assertEqual(out["matched_patterns"], ["run_week.ps1"])

    def test_nested_dispatch_refused_before_config_load(self):
        # No DELEGATE_CONFIG at all: the nested check must fire FIRST, so
        # the error is the nesting refusal, not a missing-config error.
        rc, out = run_cli(["--model", "fake/alpha", "--workspace", self.ws,
                           "--task", "hi"],
                          config=None, home=None,
                          extra_env={"PROCTOR_CHILD": "1"})
        self.assertEqual(rc, 64)
        self.assertIn("nested_dispatch_refused", out["error"])

    def test_write_without_model_refused(self):
        rc, out = run_cli(["--agent", "stub", "--workspace", self.ws,
                           "--task", "hi", "--write"],
                          config=self.config, home=self.home)
        self.assertEqual(rc, 64)
        self.assertIn("--write applies only to --model", out["error"])

    def test_model_dispatch_success_envelope(self):
        rc, out = self._run("fake/alpha", task="do the thing")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["agent"], "model:fake/alpha")
        self.assertEqual(out["model"], "fake/alpha")
        self.assertFalse(out["write_requested"])
        self.assertIn("model=fake/alpha", out["stdout"])
        self.assertIn("task=do the thing", out["stdout"])
        # QC E4: the marker is INJECTED into the child env.
        self.assertIn("marker=1", out["stdout"])

    def test_write_flag_is_reflected_on_the_envelope(self):
        rc, out = self._run("fake/alpha", write=True)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["write_requested"])

    def test_agent_mode_unchanged(self):
        rc, out = run_cli(["--agent", "stub", "--workspace", self.ws,
                           "--task", "hi"],
                          config=self.config, home=self.home)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["agent"], "stub")
        self.assertNotIn("model", out)


if __name__ == "__main__":
    unittest.main()
