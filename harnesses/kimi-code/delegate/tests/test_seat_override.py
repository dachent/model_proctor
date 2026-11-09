#!/usr/bin/env python3
"""seat_override.py tests (skill `[use <model> substantial]`).

A fixture roster + fixture KIMI_CODE_HOME catalog, so nothing here reads or
writes the machine's live agents.json. The two properties that matter:

1. The live roster is never modified — an override is a derived copy in
   scratch, so a worker cannot use one task's override to change what the next
   task dispatches.
2. The override is visible in evidence — the derived seat name carries the
   model slug, because the runner journal and task record name the seat.
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
TOOL = _DELEGATE_DIR / "seat_override.py"
sys.path.insert(0, str(_DELEGATE_DIR))

LIVE_SUBSTANTIAL = "fireworks/glm-5p3"
HY4 = "openrouter/tencent/hy4"


def make_roster(tmp):
    exe = str(_DELEGATE_DIR / "delegate.py")  # absolute; existence is lazy
    cfg = {
        "allowed_workspace_roots": [str(Path(tmp) / "ws")],
        "max_task_bytes": 262144,
        "max_timeout_seconds": 7200,
        "default_kill_grace_seconds": 5,
        "max_stdout_bytes": 65536,
        "max_stderr_bytes": 65536,
        "agents": {
            "marathon-worker": {"command": [exe, "-m", "fireworks/deepseek-v4p1-flash-1017", "-p"],
                                "prompt_delivery": "argument",
                                "default_timeout": 1800, "minimum_timeout": 60,
                                "maximum_timeout": 3600, "write_allowed": False},
            "substantial-worker": {"command": [exe, "-m", LIVE_SUBSTANTIAL, "-p"],
                                   "prompt_delivery": "argument",
                                   "default_timeout": 1800, "minimum_timeout": 60,
                                   "maximum_timeout": 3600, "write_allowed": False},
            "expert-worker": {"command": [exe, "-m", "fireworks/kimi-k3p1-pro", "-p"],
                              "prompt_delivery": "argument",
                              "default_timeout": 1800, "minimum_timeout": 60,
                              "maximum_timeout": 3600, "write_allowed": False},
        },
        "model_dispatch_template": {
            "command": [exe, "-m", "{model}", "-p"],
            "prompt_delivery": "argument",
            "resume_args": ["-r", "{session_id}"],
            "default_timeout": 1800, "minimum_timeout": 60,
            "maximum_timeout": 3600,
            "environment_allowlist": ["HOME"],
            "environment": {},
            "required_environment": [],
            "write_allowed": False,
            "allow_breakaway": False,
            "on_timeout": "kill_tree",
        },
    }
    path = Path(tmp) / "agents.json"
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return str(path)


def make_home(tmp, models):
    home = Path(tmp) / "home"
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        "".join(f'[models."{m}"]\nprovider = "fireworks"\n' for m in models),
        encoding="utf-8")
    return str(home)


class SeatOverrideCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="seat-override-")
        self.roster = make_roster(self.tmp)
        self.home = make_home(self.tmp, [LIVE_SUBSTANTIAL, HY4])
        self.out = str(Path(self.tmp) / "seats")
        self.before = Path(self.roster).read_bytes()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *extra, home=None):
        env = dict(os.environ)
        env["KIMI_CODE_HOME"] = home or self.home
        env["DELEGATE_CONFIG"] = self.roster
        env["MODEL_PROCTOR_SCRATCH"] = str(Path(self.tmp) / "scratch")
        r = subprocess.run([sys.executable, str(TOOL), *extra],
                           capture_output=True, text=True, env=env, timeout=60)
        return r.returncode, r.stdout, r.stderr

    def test_default_is_no_override(self):
        rc, out, err = self._run()
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertFalse(data["override"])
        self.assertEqual(data["model"], LIVE_SUBSTANTIAL)
        self.assertFalse(Path(self.out).exists())

    def test_hy4_override_derives_a_seat_and_leaves_the_roster_alone(self):
        rc, out, err = self._run("--model", "hy4", "--out-dir", self.out)
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertTrue(data["override"])
        self.assertEqual(data["model"], HY4)
        self.assertEqual(data["previous_model"], LIVE_SUBSTANTIAL)
        self.assertEqual(data["lane"], "glm")
        self.assertEqual(data["journal_agent"],
                         "substantial-worker-override-openrouter-tencent-hy4")
        self.assertEqual(data["env"]["DELEGATE_CONFIG"], data["delegate_config"])
        self.assertEqual(data["dispatch_args"], ["--agent-map", data["agent_map"]])
        self.assertEqual(data["metering"], "priced")
        self.assertEqual(Path(self.roster).read_bytes(), self.before)

        derived = json.loads(Path(data["delegate_config"]).read_text(encoding="utf-8"))
        alias = derived["agents"][data["journal_agent"]]
        self.assertIn(HY4, alias["command"])
        self.assertFalse(alias["write_allowed"])
        self.assertFalse(alias["allow_breakaway"])
        self.assertEqual(alias["on_timeout"], "kill_tree")
        # the live seat is carried unchanged in the copy
        self.assertIn(LIVE_SUBSTANTIAL, derived["agents"]["substantial-worker"]["command"])
        self.assertEqual(json.loads(Path(data["agent_map"]).read_text(encoding="utf-8")),
                         {"glm": data["journal_agent"]})

    def test_derived_config_loads_through_the_delegate_validator(self):
        rc, out, err = self._run("--model", "hy4", "--out-dir", self.out)
        self.assertEqual(rc, 0, err)
        derived = json.loads(out)["delegate_config"]
        rc2 = subprocess.run(
            [sys.executable, str(_DELEGATE_DIR / "delegate.py"), "--print-timeout-knobs"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "DELEGATE_CONFIG": derived})
        self.assertEqual(rc2.returncode, 0, rc2.stdout + rc2.stderr)
        self.assertTrue(json.loads(rc2.stdout)["knobs_valid"])

    def test_unknown_shorthand_is_refused_not_guessed(self):
        rc, out, _ = self._run("--model", "hy9", "--out-dir", self.out)
        self.assertEqual(rc, 2)
        data = json.loads(out)
        self.assertEqual(data["error"], "model_shorthand_unknown")
        self.assertFalse(Path(self.out).exists())

    def test_unsupported_seat_is_refused(self):
        rc, out, _ = self._run("--model", "hy4", "--seat", "marathon")
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out)["error"], "seat_not_supported")

    def test_model_outside_the_live_catalog_is_refused(self):
        rc, out, _ = self._run("--model", "openrouter/tencent/hy4-mini",
                               "--out-dir", self.out)
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out)["error"], "model_not_in_harness_config")

    def test_absent_seat_is_refused(self):
        cfg = json.loads(Path(self.roster).read_text(encoding="utf-8"))
        del cfg["agents"]["substantial-worker"]
        Path(self.roster).write_text(json.dumps(cfg), encoding="utf-8")
        rc, out, _ = self._run("--model", "hy4")
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out)["error"], "seat_not_in_roster")

    def test_unpriced_model_is_reported_as_metering_unknown(self):
        home = make_home(str(Path(self.tmp) / "home2"), ["fake/unpriced"])
        rc, out, err = self._run("--model", "fake/unpriced", "--out-dir", self.out,
                                 home=home)
        self.assertEqual(rc, 0, err)
        self.assertIn("unknown", json.loads(out)["metering"])


if __name__ == "__main__":
    unittest.main()
