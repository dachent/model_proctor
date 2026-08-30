#!/usr/bin/env python3
"""Tests for cascade/cascade.py.

End-to-end tests drive the CLI as a subprocess with CASCADE_DELEGATE pointed
at tests/fake_delegate.py (canned envelopes; no real CLIs are ever launched).
Unit tests import cascade.py directly for the validator and the atomic writer.

Run: python -m unittest discover -s cascade/tests -v
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
CASCADE_DIR = TESTS_DIR.parent
CASCADE = CASCADE_DIR / "cascade.py"
FAKE_DELEGATE = TESTS_DIR / "fake_delegate.py"
AGENTS_FIXTURE = TESTS_DIR / "fixtures" / "agents.json"
SCHEMA_FILE = CASCADE_DIR / "cascade-schema.json"
TMP_ROOT = TESTS_DIR / "tmp"

sys.path.insert(0, str(CASCADE_DIR))
import cascade as cascade_mod  # noqa: E402

PY = sys.executable.replace("\\", "/")


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class CascadeTestCase(unittest.TestCase):
    """Base: a fresh fixture workspace under cascade/tests/tmp per test."""

    def setUp(self):
        self.ws = TMP_ROOT / self.id().split(".")[-1]
        shutil.rmtree(self.ws, ignore_errors=True)
        (self.ws / "tests").mkdir(parents=True)
        (self.ws / "src").mkdir(parents=True)
        (self.ws / "tests" / "acceptance_test.py").write_text(
            "import sys\nsys.exit(0)\n", encoding="utf-8")
        (self.ws / "check_fail.py").write_text(
            "import sys\nprint('deterministic failure text ABC')\nsys.exit(1)\n",
            encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    # -- helpers ------------------------------------------------------------

    def env(self, mode="completed"):
        env = dict(os.environ)
        env["CASCADE_DELEGATE"] = str(FAKE_DELEGATE)
        env["CASCADE_AGENTS_CONFIG"] = str(AGENTS_FIXTURE)
        env["FAKE_DELEGATE_MODE"] = mode
        env["FAKE_DELEGATE_CALLS"] = str(self.ws / "calls.jsonl")
        env["CASCADE_RETRY_BACKOFF"] = "0"
        return env

    def run_cascade(self, *argv, mode="completed"):
        return subprocess.run(
            [sys.executable, str(CASCADE), *[str(a) for a in argv]],
            capture_output=True, text=True, env=self.env(mode), timeout=120,
        )

    def make_plan(self, tasks=None, verify_cmd=None, **overrides):
        if verify_cmd is None:
            verify_cmd = f'"{PY}" tests/acceptance_test.py'
        if tasks is None:
            tasks = [{
                "task_id": "t1",
                "objective": "implement the thing",
                "executor": "flash",
                "verification": {"deterministic": verify_cmd},
                "scope": ["src"],
                "criticality": "normal",
                "max_attempts": 2,
            }]
        plan = {
            "goal": "fixture goal",
            "cost_ceiling_usd": 1.0,
            "k3_direct_cost_estimate_usd": 5.0,
            "verifier_set": ["tests/acceptance_test.py"],
            "tasks": tasks,
        }
        plan.update(overrides)
        return plan

    def write_plan(self, plan, name="plan.json"):
        path = self.ws / name
        path.write_text(json.dumps(plan), encoding="utf-8")
        return path

    def init(self, plan=None, threat_model="single-operator"):
        plan = plan or self.make_plan()
        plan_file = self.write_plan(plan)
        result = self.run_cascade("init", "--workspace", self.ws,
                                  "--plan-file", plan_file,
                                  "--threat-model", threat_model)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def git(self, *argv, check=True):
        result = subprocess.run(
            ["git", "-C", str(self.ws), *[str(a) for a in argv]],
            capture_output=True, text=True, timeout=60,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def git_init_repo(self):
        """Turn the fixture workspace into a git repo with one baseline commit.

        Orchestrator scratch (state dir, fake-delegate call log, plan files) is
        gitignored so untracked-inventory assertions see only worker writes.
        """
        if shutil.which("git") is None:
            self.skipTest("git not available")
        (self.ws / ".gitignore").write_text(
            ".orchestrator/\ncalls.jsonl\nplan.json\nreplan.json\n",
            encoding="utf-8")
        self.git("init")
        self.git("add", "-A")
        self.git("-c", "user.email=test@example.com", "-c", "user.name=Test",
                 "commit", "-m", "baseline")

    def state(self):
        return _read_json(self.ws / ".orchestrator" / "cascade-state.json")

    def save_state(self, state):
        (self.ws / ".orchestrator" / "cascade-state.json").write_text(
            json.dumps(state, indent=2), encoding="utf-8")

    def log_lines(self):
        path = self.ws / ".orchestrator" / "cascade-log.jsonl"
        return [json.loads(l) for l in
                path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def fake_calls(self):
        path = self.ws / "calls.jsonl"
        if not path.is_file():
            return []
        return [json.loads(l) for l in
                path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def dispatch(self, task="t1", mode="completed", extra=()):
        return self.run_cascade("dispatch", "--workspace", self.ws,
                                "--task", task, *extra, mode=mode)


# ---------------------------------------------------------------------------
# Schema validation (unit, via validate_plan; plus CLI-level rejection)
# ---------------------------------------------------------------------------

class TestSchemaValidation(CascadeTestCase):
    def validate(self, plan):
        return cascade_mod.validate_plan(plan, self.ws, {"ds-flash-worker",
                                                         "ds-pro-worker",
                                                         "k27-worker"})

    def test_valid_plan_passes(self):
        self.assertEqual(self.validate(self.make_plan()), [])

    def test_unknown_executor_rejected(self):
        plan = self.make_plan()
        plan["tasks"][0]["executor"] = "grok"
        errors = self.validate(plan)
        self.assertTrue(any("executor" in e for e in errors), errors)

    def test_executor_profile_missing_from_agents_config_rejected(self):
        plan = self.make_plan()
        plan["tasks"][0]["executor"] = "pro"
        plan["tasks"][0]["pro_reason"] = "cross-file reasoning"
        errors = cascade_mod.validate_plan(plan, self.ws, {"ds-flash-worker"})
        self.assertTrue(any("ds-pro-worker" in e for e in errors), errors)

    def test_pro_without_reason_rejected(self):
        plan = self.make_plan()
        plan["tasks"][0]["executor"] = "pro"
        plan["tasks"][0]["pro_reason"] = None
        errors = self.validate(plan)
        self.assertTrue(any("pro_reason" in e for e in errors), errors)

    def test_missing_verifier_file_rejected(self):
        plan = self.make_plan(verifier_set=["tests/does_not_exist.py"])
        errors = self.validate(plan)
        self.assertTrue(any("verifier file not found" in e for e in errors), errors)

    def test_verifier_path_escape_rejected(self):
        plan = self.make_plan(verifier_set=["../outside.py"])
        errors = self.validate(plan)
        self.assertTrue(any("escapes" in e for e in errors), errors)

    def test_malformed_entries_rejected(self):
        self.assertTrue(self.validate({"goal": "g"}))           # missing fields
        self.assertTrue(self.validate(self.make_plan(tasks=[])))  # empty tasks
        self.assertTrue(self.validate(self.make_plan(tasks="nope")))
        bad = self.make_plan()
        bad["tasks"][0]["criticality"] = "extreme"
        self.assertTrue(any("criticality" in e for e in self.validate(bad)))
        bad = self.make_plan()
        bad["tasks"][0]["max_attempts"] = 3
        self.assertTrue(any("max_attempts" in e for e in self.validate(bad)))
        bad = self.make_plan()
        bad["tasks"].append(dict(bad["tasks"][0]))  # duplicate task_id
        self.assertTrue(any("duplicated" in e for e in self.validate(bad)))
        bad = self.make_plan()
        bad["tasks"][0]["verification"] = {"deterministic": "x", "qc_review": True}
        self.assertTrue(any("verification" in e for e in self.validate(bad)))
        bad = self.make_plan(cost_ceiling_usd=-1)
        self.assertTrue(any("cost_ceiling_usd" in e for e in self.validate(bad)))

    def test_cli_rejects_invalid_plan(self):
        plan = self.make_plan()
        plan["tasks"][0]["executor"] = "grok"
        plan_file = self.write_plan(plan)
        result = self.run_cascade("init", "--workspace", self.ws,
                                  "--plan-file", plan_file,
                                  "--threat-model", "single-operator")
        self.assertEqual(result.returncode, 2)
        out = json.loads(result.stdout)
        self.assertEqual(out["error"], "plan_validation_failed")
        self.assertTrue(out["details"])

    def test_schema_artifact_is_valid_json_and_mirrors_required_fields(self):
        schema = _read_json(SCHEMA_FILE)
        for field in ("goal", "verifier_set", "tasks", "cost_ceiling_usd"):
            self.assertIn(field, schema["required"])
        self.assertEqual(schema["$defs"]["task"]["properties"]["executor"]["enum"],
                         ["flash", "pro", "k27", "k3"])


# ---------------------------------------------------------------------------
# Atomic state write
# ---------------------------------------------------------------------------

class TestAtomicWrite(CascadeTestCase):
    def test_no_partial_file_on_crash(self):
        target = self.ws / "state.json"
        original = {"tasks": [], "marker": "original"}
        target.write_text(json.dumps(original), encoding="utf-8")
        with mock.patch.object(cascade_mod.os, "replace",
                               side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                cascade_mod._write_json_atomic(target, {"tasks": [], "marker": "new"})
        # Original file untouched, no temp files left behind.
        self.assertEqual(_read_json(target), original)
        leftovers = [p for p in self.ws.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

class TestInit(CascadeTestCase):
    def test_init_writes_state_with_hashes_and_counters(self):
        self.init()
        state = self.state()
        self.assertEqual(state["counters"]["planner_calls"], 1)
        self.assertEqual(state["counters"]["executor_attempts"], 0)
        self.assertEqual(state["cost_warning_usd"], 0.5)
        self.assertIn("tests/acceptance_test.py", state["verifier_hashes"])
        self.assertEqual(len(state["verifier_hashes"]["tests/acceptance_test.py"]), 64)
        self.assertIn(state["checkpoint"]["mode"], ("git", "file_hashes"))
        task = state["tasks"][0]
        self.assertEqual(task["status"], "ready")
        self.assertEqual(task["attempts"], 0)
        self.assertEqual(task["rung"], 0)

    def test_init_twice_refused(self):
        self.init()
        plan_file = self.write_plan(self.make_plan(), "plan2.json")
        result = self.run_cascade("init", "--workspace", self.ws,
                                  "--plan-file", plan_file,
                                  "--threat-model", "single-operator")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"], "already_initialized")


# ---------------------------------------------------------------------------
# dispatch: legality matrix, resume, ladder, caps
# ---------------------------------------------------------------------------

class TestDispatchLegality(CascadeTestCase):
    def test_unknown_task_refused(self):
        self.init()
        result = self.dispatch(task="nope")
        self.assertEqual(result.returncode, 3)
        out = json.loads(result.stdout)
        self.assertFalse(out["allowed"])
        self.assertEqual(out["reason"], "unknown_task")

    def test_dispatch_ready_task_ok_and_updates_state(self):
        self.init()
        result = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["status"], "completed")
        self.assertEqual(envelope["child_session_id"], "fake-session-0001")
        state = self.state()
        task = state["tasks"][0]
        self.assertEqual(task["attempts"], 1)
        self.assertEqual(task["rung_attempts"], 1)
        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(task["resume_session_id"], "fake-session-0001")
        self.assertEqual(state["counters"]["executor_attempts"], 1)
        # Packet contains objective/scope/verifier.
        packet = self.fake_calls()[0]["task"]
        self.assertIn("implement the thing", packet)
        self.assertIn("acceptance_test.py", packet)
        self.assertIn("src", packet)

    def test_dispatch_done_task_refused(self):
        self.init()
        state = self.state()
        state["tasks"][0]["status"] = "done"
        self.save_state(state)
        result = self.dispatch()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reason"],
                         "task_status_not_dispatchable")

    def test_within_rung_retry_uses_resume_from(self):
        self.init()
        self.dispatch()
        self.dispatch()
        calls = self.fake_calls()
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0]["resume_from"])
        self.assertEqual(calls[1]["resume_from"], "fake-session-0001")

    def test_evidence_from_prior_attempt_in_retry_packet(self):
        self.init()
        self.dispatch()
        self.dispatch()
        packet = self.fake_calls()[1]["task"]
        self.assertIn("Evidence from prior attempts", packet)
        self.assertIn("fake stdout", packet)


class TestEscalationLadder(CascadeTestCase):
    def test_full_ladder_normal_criticality(self):
        """assigned(2) -> k3(2) -> advisor(1) -> post-advisor k3(1) -> stop (cap 5)."""
        self.init()
        rungs = []
        # d1, d2: assigned rung (flash via fake delegate)
        for _ in range(2):
            r = self.dispatch()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            rungs.append(self.state()["tasks"][0]["rung"])
        # d3: auto-escalation to native K3
        r = self.dispatch()
        self.assertEqual(json.loads(r.stdout)["status"], "native_dispatch")
        rungs.append(self.state()["tasks"][0]["rung"])
        # d4: second K3 attempt
        r = self.dispatch()
        self.assertEqual(json.loads(r.stdout)["status"], "native_dispatch")
        rungs.append(self.state()["tasks"][0]["rung"])
        # d5: advisor rung (flat-rate; does NOT consume executor attempts)
        attempts_before = self.state()["tasks"][0]["attempts"]
        r = self.dispatch()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        state = self.state()
        self.assertEqual(state["tasks"][0]["attempts"], attempts_before)
        self.assertEqual(state["counters"]["advisor_calls"], 1)
        rungs.append(state["tasks"][0]["rung"])
        self.assertEqual(self.fake_calls()[-1]["agent"], "codex-advisor")
        # d6: post-advisor K3 retry (the 5th metered attempt)
        r = self.dispatch()
        self.assertEqual(json.loads(r.stdout)["status"], "native_dispatch")
        state = self.state()
        self.assertEqual(state["tasks"][0]["attempts"], 5)
        rungs.append(state["tasks"][0]["rung"])
        self.assertEqual(rungs, [0, 0, 1, 1, 2, 3])
        # d7: ladder exhausted -> refusal, task stopped
        r = self.dispatch()
        self.assertEqual(r.returncode, 3)
        self.assertEqual(json.loads(r.stdout)["reason"], "ladder_exhausted")
        state = self.state()
        self.assertEqual(state["tasks"][0]["status"], "failed")
        self.assertEqual(state["counters"]["executor_attempts"], 5)

    def test_full_ladder_high_criticality_cap_6(self):
        plan = self.make_plan()
        plan["tasks"][0]["criticality"] = "high"
        self.init(plan)
        for _ in range(4):  # 2 assigned + 2 k3
            self.assertEqual(self.dispatch().returncode, 0)
        self.assertEqual(self.dispatch().returncode, 0)  # advisor
        # two post-advisor K3 retries allowed at high criticality
        self.assertEqual(self.dispatch().returncode, 0)
        self.assertEqual(self.dispatch().returncode, 0)
        state = self.state()
        self.assertEqual(state["tasks"][0]["attempts"], 6)
        r = self.dispatch()
        self.assertEqual(r.returncode, 3)
        self.assertEqual(json.loads(r.stdout)["reason"], "ladder_exhausted")

    def test_forced_escalate_flag(self):
        self.init()
        self.dispatch(extra=("--escalate",))
        state = self.state()
        self.assertEqual(state["tasks"][0]["rung"], 1)
        self.assertEqual(len(self.fake_calls()), 0)  # k3 rung is native

    def test_escalation_is_always_fresh_dispatch(self):
        self.init()
        self.dispatch()  # rung 0 via fake; stores resume_session_id
        self.assertEqual(self.state()["tasks"][0]["resume_session_id"],
                         "fake-session-0001")
        self.dispatch(extra=("--escalate",))  # rung 1: native k3, no fake call
        self.dispatch(extra=("--escalate",))  # rung 2: advisor via fake
        calls = self.fake_calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1]["agent"], "codex-advisor")
        self.assertIsNone(calls[-1]["resume_from"])  # escalation never resumes


class TestCostCeiling(CascadeTestCase):
    def test_ceiling_exceeded_stops_dispatch(self):
        self.init()
        state = self.state()
        state["cost_used_usd"] = 1.0  # == ceiling
        self.save_state(state)
        result = self.dispatch()
        self.assertEqual(result.returncode, 3)
        out = json.loads(result.stdout)
        self.assertEqual(out["reason"], "cost_ceiling_exceeded")
        self.assertEqual(self.fake_calls(), [])

    def test_warning_at_50_percent(self):
        self.init()
        state = self.state()
        state["cost_used_usd"] = 0.6  # >= 0.5 warning, < 1.0 ceiling
        self.save_state(state)
        result = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING", result.stderr)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

class TestVerify(CascadeTestCase):
    def test_tamper_detection_rejects_and_marks_escalation(self):
        self.init()
        # Worker modifies the frozen verifier file between init and verify.
        (self.ws / "tests" / "acceptance_test.py").write_text(
            "import sys\nsys.exit(0)  # weakened\n", encoding="utf-8")
        result = self.run_cascade("verify", "--workspace", self.ws, "--task", "t1")
        self.assertEqual(result.returncode, 1)
        out = json.loads(result.stdout)
        self.assertFalse(out["passed"])
        self.assertEqual(out["reason"], "verifier_modified")
        self.assertEqual(out["modified"][0]["path"], "tests/acceptance_test.py")
        state = self.state()
        self.assertTrue(state["tasks"][0]["force_escalate"])
        # Never accepted:
        self.assertFalse(state["tasks"][0]["verification_passed"])
        # Next dispatch escalates instead of continuing the rung.
        r = self.dispatch()
        self.assertEqual(json.loads(r.stdout)["status"], "native_dispatch")
        self.assertEqual(self.state()["tasks"][0]["rung"], 1)

    def test_missing_verifier_file_is_tamper(self):
        self.init()
        (self.ws / "tests" / "acceptance_test.py").unlink()
        result = self.run_cascade("verify", "--workspace", self.ws, "--task", "t1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["modified"][0]["change"], "missing")

    def test_clean_verify_runs_command_and_records_pass(self):
        self.init()
        self.dispatch()
        result = self.run_cascade("verify", "--workspace", self.ws, "--task", "t1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        out = json.loads(result.stdout)
        self.assertTrue(out["passed"])
        state = self.state()
        self.assertTrue(state["tasks"][0]["verification_passed"])
        self.assertEqual(state["tasks"][0]["verify_result"]["exit_code"], 0)

    def test_failing_verifier_records_evidence(self):
        plan = self.make_plan(verify_cmd=f'"{PY}" check_fail.py')
        self.init(plan)
        self.dispatch()
        result = self.run_cascade("verify", "--workspace", self.ws, "--task", "t1")
        self.assertEqual(result.returncode, 1)
        out = json.loads(result.stdout)
        self.assertFalse(out["passed"])
        self.assertEqual(out["exit_code"], 1)
        state = self.state()
        self.assertFalse(state["tasks"][0]["verification_passed"])
        self.assertIn("deterministic failure text ABC",
                      state["tasks"][0]["evidence"][-1]["detail"])

    def test_verifier_defect_suspect_on_same_failure_across_two_executors(self):
        plan = self.make_plan(verify_cmd=f'"{PY}" check_fail.py')
        self.init(plan)
        self.dispatch()
        self.run_cascade("verify", "--workspace", self.ws, "--task", "t1")
        self.assertFalse(self.state()["verifier_defect_suspect"])
        self.dispatch(extra=("--escalate",))  # different executor (k3 rung)
        self.run_cascade("verify", "--workspace", self.ws, "--task", "t1")
        self.assertTrue(self.state()["verifier_defect_suspect"])

    def test_qc_review_task_verify_is_immutability_only(self):
        plan = self.make_plan()
        plan["tasks"][0]["verification"] = {"qc_review": True}
        self.init(plan)
        result = self.run_cascade("verify", "--workspace", self.ws, "--task", "t1")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["reason"],
                         "no_deterministic_verifier")


# ---------------------------------------------------------------------------
# record-qc
# ---------------------------------------------------------------------------

FINDINGS_OK = json.dumps([{"severity": "major", "location": "src/a.py:10",
                           "claim": "off-by-one", "evidence": "loop bound",
                           "minimal_fix": "use <="}])

class TestRecordQC(CascadeTestCase):
    def test_reject_without_findings_refused(self):
        self.init()
        result = self.run_cascade("record-qc", "--workspace", self.ws,
                                  "--task", "t1", "--verdict", "reject")
        self.assertEqual(result.returncode, 2)

    def test_reject_with_malformed_findings_refused(self):
        self.init()
        bad = json.dumps([{"severity": "major", "location": "src/a.py:10"}])  # no claim
        result = self.run_cascade("record-qc", "--workspace", self.ws,
                                  "--task", "t1", "--verdict", "reject",
                                  "--findings", bad)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"], "invalid_findings")

    def test_reject_with_findings_recorded(self):
        self.init()
        result = self.run_cascade("record-qc", "--workspace", self.ws,
                                  "--task", "t1", "--verdict", "reject",
                                  "--findings", FINDINGS_OK,
                                  "--root-cause", "decomp")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = self.state()
        task = state["tasks"][0]
        self.assertEqual(task["qc_reviews"], 1)
        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(task["root_cause"], "decomp")
        self.assertEqual(state["counters"]["qc_reviews"], 1)

    def test_findings_from_file(self):
        self.init()
        f = self.ws / "findings.json"
        f.write_text(FINDINGS_OK, encoding="utf-8")
        result = self.run_cascade("record-qc", "--workspace", self.ws,
                                  "--task", "t1", "--verdict", "reject",
                                  "--findings", str(f))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_accept_marks_done(self):
        self.init()
        result = self.run_cascade("record-qc", "--workspace", self.ws,
                                  "--task", "t1", "--verdict", "accept")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.state()["tasks"][0]["status"], "done")

    def test_accept_with_open_blocker_finding_refused(self):
        self.init()
        findings = json.dumps([{"severity": "blocker", "location": "src/a.py",
                                "claim": "data loss"}])
        result = self.run_cascade("record-qc", "--workspace", self.ws,
                                  "--task", "t1", "--verdict",
                                  "accept-with-minor-fixes", "--findings", findings)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reason"],
                         "open_blocker_major_findings")

    def test_qc_cap_normal_2(self):
        self.init()
        for _ in range(2):
            r = self.run_cascade("record-qc", "--workspace", self.ws,
                                 "--task", "t1", "--verdict", "reject",
                                 "--findings", FINDINGS_OK)
            self.assertEqual(r.returncode, 0, r.stdout)
        r = self.run_cascade("record-qc", "--workspace", self.ws,
                             "--task", "t1", "--verdict", "reject",
                             "--findings", FINDINGS_OK)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(json.loads(r.stdout)["reason"], "qc_cap_exhausted")
        state = self.state()
        self.assertEqual(state["tasks"][0]["status"], "failed")
        self.assertEqual(state["tasks"][0]["failure_reason"], "qc_cap_exhausted")

    def test_qc_cap_high_3(self):
        plan = self.make_plan()
        plan["tasks"][0]["criticality"] = "high"
        self.init(plan)
        for _ in range(3):
            r = self.run_cascade("record-qc", "--workspace", self.ws,
                                 "--task", "t1", "--verdict", "reject",
                                 "--findings", FINDINGS_OK)
            self.assertEqual(r.returncode, 0, r.stdout)
        r = self.run_cascade("record-qc", "--workspace", self.ws,
                             "--task", "t1", "--verdict", "reject",
                             "--findings", FINDINGS_OK)
        self.assertEqual(r.returncode, 3)


# ---------------------------------------------------------------------------
# delegate envelope / transport failures
# ---------------------------------------------------------------------------

class TestDelegateFailures(CascadeTestCase):
    def test_envelope_parse_failure_not_counted(self):
        self.init()
        result = self.dispatch(mode="garbage")
        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stdout)["error"],
                         "delegate_envelope_parse_error")
        state = self.state()
        self.assertEqual(state["tasks"][0]["attempts"], 0)  # infra, not an attempt
        self.assertEqual(state["counters"]["executor_attempts"], 0)
        self.assertEqual(self.log_lines()[-1]["delegate_status"],
                         "envelope_parse_error")

    def test_internal_error_retried_once_not_counted(self):
        self.init()
        result = self.dispatch(mode="internal_error")
        self.assertEqual(result.returncode, 4)
        self.assertEqual(len(self.fake_calls()), 2)  # one retry with backoff
        self.assertEqual(self.state()["tasks"][0]["attempts"], 0)

    def test_timeout_counts_as_attempt(self):
        self.init()
        result = self.dispatch(mode="timeout")
        self.assertEqual(result.returncode, 0)  # envelope is authoritative
        self.assertEqual(json.loads(result.stdout)["status"], "timeout")
        state = self.state()
        self.assertEqual(state["tasks"][0]["attempts"], 1)
        self.assertEqual(self.log_lines()[-1]["delegate_status"], "timeout")


# ---------------------------------------------------------------------------
# log format and status
# ---------------------------------------------------------------------------

class TestLogAndStatus(CascadeTestCase):
    def test_log_append_format(self):
        self.init()
        self.dispatch(extra=("--reason", "first try"))
        entry = self.log_lines()[-1]
        self.assertEqual(entry["event"], "dispatch")
        self.assertEqual(entry["task_id"], "t1")
        self.assertEqual(entry["executor"], "flash")
        self.assertEqual(entry["token_class"], "execution")
        self.assertEqual(entry["delegate_status"], "completed")
        self.assertIsNotNone(entry["duration_seconds"])
        self.assertIsNone(entry["cost_usd"])  # metering is meter.py's job
        self.assertEqual(entry["tokens"],
                         {"uncached_in": None, "cached_in": None, "out": None})
        self.assertEqual(entry["trigger"], "first try")
        self.assertEqual(entry["child_session_id"], "fake-session-0001")
        self.assertIn("timestamp", entry)

    def test_status_line(self):
        self.init()
        self.dispatch()
        result = self.run_cascade("status", "--workspace", self.ws)
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        out = json.loads(lines[0])
        self.assertEqual(out["tasks_done"], 0)
        self.assertEqual(out["tasks_total"], 1)
        self.assertEqual(out["counters"]["executor_attempts"], 1)
        self.assertEqual(out["cost_used_usd"], 0.0)
        self.assertEqual(out["cost_ceiling_usd"], 1.0)
        self.assertFalse(out["cost_warning"])
        self.assertEqual(out["open_tasks"][0]["rung_name"], "assigned")
        self.assertEqual(out["open_tasks"][0]["attempt_cap"], 5)


# ---------------------------------------------------------------------------
# replan
# ---------------------------------------------------------------------------

class TestReplan(CascadeTestCase):
    def _two_task_plan(self):
        tasks = [
            {"task_id": "t1", "objective": "one", "executor": "flash",
             "verification": {"qc_review": True}, "scope": ["src"]},
            {"task_id": "t2", "objective": "two", "executor": "flash",
             "verification": {"qc_review": True}, "scope": ["src"]},
        ]
        return self.make_plan(tasks=tasks)

    def _fail_two_tasks_same_cause(self):
        state = self.state()
        for t in state["tasks"]:
            t["status"] = "failed"
            t["root_cause"] = "decomposition-error"
        self.save_state(state)

    def _new_plan_file(self):
        plan = self.make_plan(tasks=[{
            "task_id": "t3", "objective": "replacement", "executor": "flash",
            "verification": {"qc_review": True}, "scope": ["src"]}])
        return self.write_plan(plan, "replan.json")

    def test_replan_requires_confirm(self):
        self.init(self._two_task_plan())
        self._fail_two_tasks_same_cause()
        result = self.run_cascade("replan", "--workspace", self.ws,
                                  "--plan-file", self._new_plan_file(),
                                  "--root-cause", "decomposition-error",
                                  "--reason", "bad decomposition")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reason"],
                         "replan_requires_confirm")

    def test_replan_requires_two_failures_same_root_cause(self):
        self.init(self._two_task_plan())
        state = self.state()
        state["tasks"][0]["status"] = "failed"
        state["tasks"][0]["root_cause"] = "decomposition-error"
        self.save_state(state)  # only one failed task
        result = self.run_cascade("replan", "--workspace", self.ws,
                                  "--plan-file", self._new_plan_file(),
                                  "--root-cause", "decomposition-error",
                                  "--reason", "x", "--confirm")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reason"],
                         "replan_precondition_not_met")

    def test_replan_replaces_tasks_and_counts(self):
        self.init(self._two_task_plan())
        self._fail_two_tasks_same_cause()
        result = self.run_cascade("replan", "--workspace", self.ws,
                                  "--plan-file", self._new_plan_file(),
                                  "--root-cause", "decomposition-error",
                                  "--reason", "bad decomposition", "--confirm")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = self.state()
        self.assertEqual([t["task_id"] for t in state["tasks"]], ["t3"])
        self.assertEqual(state["counters"]["replan_calls"], 1)
        self.assertEqual(self.log_lines()[-1]["event"], "replan")

    def test_planner_replan_cap_2_per_goal(self):
        self.init(self._two_task_plan())
        self._fail_two_tasks_same_cause()
        self.assertEqual(self.run_cascade(
            "replan", "--workspace", self.ws,
            "--plan-file", self._new_plan_file(),
            "--root-cause", "decomposition-error",
            "--reason", "x", "--confirm").returncode, 0)
        # planner_calls(1) + replan_calls(1) == cap 2 -> next replan refused
        state = self.state()
        state["tasks"][0]["status"] = "failed"
        state["tasks"][0]["root_cause"] = "decomposition-error"
        state["tasks"].append(dict(state["tasks"][0], task_id="t4"))
        self.save_state(state)
        result = self.run_cascade("replan", "--workspace", self.ws,
                                  "--plan-file", self._new_plan_file(),
                                  "--root-cause", "decomposition-error",
                                  "--reason", "y", "--confirm")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reason"],
                         "planner_replan_cap_exhausted")


# ---------------------------------------------------------------------------
# Vision capability routing (spec §9.1 v3.1)
# ---------------------------------------------------------------------------

class TestVisionRouting(CascadeTestCase):
    def plan_with_task(self, executor, scope, vision=None):
        task = {"task_id": "t1", "objective": "handle the image",
                "executor": executor, "verification": {"qc_review": True},
                "scope": scope}
        if executor == "pro":
            task["pro_reason"] = "cross-file reasoning"
        if vision is not None:
            task["vision"] = vision
        return self.make_plan(tasks=[task])

    def init_expect_reject(self, plan):
        plan_file = self.write_plan(plan)
        result = self.run_cascade("init", "--workspace", self.ws,
                                  "--plan-file", plan_file,
                                  "--threat-model", "single-operator")
        self.assertEqual(result.returncode, 2, result.stdout)
        return json.loads(result.stdout)

    def test_init_rejects_flash_with_image_scope_file(self):
        out = self.init_expect_reject(
            self.plan_with_task("flash", ["assets/logo.png"]))
        self.assertEqual(out["error"], "plan_validation_failed")
        self.assertTrue(any("vision-capable" in d for d in out["details"]), out)

    def test_init_rejects_pro_with_image_scope_file(self):
        out = self.init_expect_reject(
            self.plan_with_task("pro", ["assets/logo.PNG"]))  # case-insensitive
        self.assertTrue(any("vision-capable" in d for d in out["details"]), out)

    def test_init_rejects_image_inside_scope_directory(self):
        (self.ws / "assets").mkdir()
        (self.ws / "assets" / "screenshot.webp").write_bytes(b"\x00" * 16)
        out = self.init_expect_reject(self.plan_with_task("flash", ["assets"]))
        self.assertTrue(any("vision-capable" in d for d in out["details"]), out)

    def test_init_rejects_flash_with_explicit_vision_flag(self):
        out = self.init_expect_reject(self.plan_with_task("flash", ["src"], vision=True))
        self.assertTrue(any("vision-capable" in d for d in out["details"]), out)

    def test_init_rejects_non_boolean_vision_flag(self):
        out = self.init_expect_reject(self.plan_with_task("k27", ["src"], vision="yes"))
        self.assertTrue(any(".vision must be a boolean" in d for d in out["details"]), out)

    def test_init_accepts_k27_with_image_scope(self):
        self.init(self.plan_with_task("k27", ["assets/logo.png"]))
        self.assertEqual(self.state()["tasks"][0]["executor"], "k27")

    def test_init_accepts_k3_with_vision_flag(self):
        self.init(self.plan_with_task("k3", ["src"], vision=True))
        task = self.state()["tasks"][0]
        self.assertEqual(task["executor"], "k3")
        self.assertTrue(task["vision"])

    def test_init_accepts_flash_for_text_only_scope(self):
        self.init(self.plan_with_task("flash", ["src"]))
        self.assertEqual(self.state()["tasks"][0]["executor"], "flash")

    def test_dispatch_rechecks_after_scope_gains_image(self):
        # Passes init: assets/ exists but contains no images yet.
        (self.ws / "assets").mkdir()
        (self.ws / "assets" / "notes.txt").write_text("text\n", encoding="utf-8")
        self.init(self.plan_with_task("flash", ["assets"]))
        # Scope contents change between init and dispatch.
        (self.ws / "assets" / "diagram.jpg").write_bytes(b"\xff\xd8\xff")
        result = self.dispatch()
        self.assertEqual(result.returncode, 3)
        out = json.loads(result.stdout)
        self.assertFalse(out["allowed"])
        self.assertEqual(out["reason"], "vision_capability_violation")
        self.assertEqual(self.fake_calls(), [])  # nothing was dispatched
        self.assertEqual(self.state()["tasks"][0]["attempts"], 0)

    def test_dispatch_allows_vision_task_on_k27(self):
        self.init(self.plan_with_task("k27", ["assets/logo.png"]))
        result = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fake_calls()[0]["agent"], "k27-worker")


# ---------------------------------------------------------------------------
# Threat model (init) and decisions log
# ---------------------------------------------------------------------------

class TestThreatModel(CascadeTestCase):
    def test_init_requires_threat_model(self):
        plan_file = self.write_plan(self.make_plan())
        result = self.run_cascade("init", "--workspace", self.ws,
                                  "--plan-file", plan_file)
        self.assertEqual(result.returncode, 2)  # argparse: required flag missing

    def test_threat_model_stored_and_surfaced(self):
        result = self.init(threat_model="adversarial-local")
        self.assertEqual(json.loads(result.stdout)["threat_model"],
                         "adversarial-local")
        self.assertEqual(self.state()["threat_model"], "adversarial-local")
        self.assertEqual(self.log_lines()[-1]["threat_model"], "adversarial-local")
        status = json.loads(self.run_cascade("status", "--workspace", self.ws).stdout)
        self.assertEqual(status["threat_model"], "adversarial-local")

    def test_threat_model_immutable_across_replan(self):
        plan = self.make_plan(tasks=[
            {"task_id": "t1", "objective": "one", "executor": "flash",
             "verification": {"qc_review": True}, "scope": ["src"]},
            {"task_id": "t2", "objective": "two", "executor": "flash",
             "verification": {"qc_review": True}, "scope": ["src"]}])
        self.init(plan, threat_model="hostile-input")
        state = self.state()
        for t in state["tasks"]:
            t["status"] = "failed"
            t["root_cause"] = "decomp"
        self.save_state(state)
        result = self.run_cascade(
            "replan", "--workspace", self.ws,
            "--plan-file", self.write_plan(self.make_plan(), "replan.json"),
            "--root-cause", "decomp", "--reason", "x", "--confirm")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.state()["threat_model"], "hostile-input")


class TestRecordDecision(CascadeTestCase):
    def test_record_decision_appends(self):
        self.init()
        result = self.run_cascade(
            "record-decision", "--workspace", self.ws,
            "--decision", "use flash for t1", "--rationale", "mechanical task",
            "--rejected", "pro (no cross-file reasoning)",
            "--rejected", "k3 (too expensive)", "--source", "leader")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        decisions = self.state()["decisions"]
        self.assertEqual(len(decisions), 1)
        d = decisions[0]
        self.assertEqual(d["decision"], "use flash for t1")
        self.assertEqual(d["rationale"], "mechanical task")
        self.assertEqual(d["rejected_alternatives"],
                         ["pro (no cross-file reasoning)", "k3 (too expensive)"])
        self.assertEqual(d["source"], "leader")
        self.assertIn("timestamp", d)
        self.assertEqual(self.log_lines()[-1]["event"], "record_decision")

    def test_record_decision_is_append_only(self):
        self.init()
        for i in range(2):
            self.run_cascade("record-decision", "--workspace", self.ws,
                             "--decision", f"d{i}", "--rationale", "r",
                             "--source", "user")
        self.assertEqual([d["decision"] for d in self.state()["decisions"]],
                         ["d0", "d1"])

    def test_record_decision_empty_fields_rejected(self):
        self.init()
        result = self.run_cascade(
            "record-decision", "--workspace", self.ws,
            "--decision", "  ", "--rationale", "r", "--source", "user")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"], "invalid_decision")


# ---------------------------------------------------------------------------
# Evidence hardening (F2/F3): files_changed, child_exit_code, log archival
# ---------------------------------------------------------------------------

class TestEvidenceHardening(CascadeTestCase):
    def test_evidence_records_exit_code_and_not_a_repo(self):
        self.init()
        self.dispatch(mode="failed")
        ev = self.state()["tasks"][0]["evidence"][-1]
        self.assertEqual(ev["status"], "failed")
        self.assertEqual(ev["child_exit_code"], 1)
        self.assertEqual(ev["files_changed"], "unavailable_not_a_git_repo")

    def test_evidence_archives_run_dir_logs_with_sha256(self):
        run_dir = self.ws / "fake_run"
        run_dir.mkdir()
        (run_dir / "stdout.log").write_bytes(b"full stdout log\n")
        (run_dir / "stderr.log").write_bytes(b"full stderr log\n")
        os.environ["FAKE_DELEGATE_RUN_DIR"] = str(run_dir)
        self.addCleanup(os.environ.pop, "FAKE_DELEGATE_RUN_DIR", None)
        self.init()
        self.dispatch()
        ev = self.state()["tasks"][0]["evidence"][-1]
        self.assertEqual(ev["run_dir"], str(run_dir))
        ev_dir = Path(ev["evidence_dir"])
        self.assertEqual(ev_dir, self.ws / ".orchestrator" / "evidence" / "t1-1")
        self.assertEqual((ev_dir / "stdout.log").read_bytes(), b"full stdout log\n")
        self.assertEqual((ev_dir / "stderr.log").read_bytes(), b"full stderr log\n")
        self.assertEqual(ev["stdout_sha256"],
                         hashlib.sha256(b"full stdout log\n").hexdigest())
        self.assertEqual(ev["stderr_sha256"],
                         hashlib.sha256(b"full stderr log\n").hexdigest())

    def test_files_changed_includes_untracked_inventory(self):
        (self.ws / "src" / "tracked.py").write_text("original\n", encoding="utf-8")
        self.git_init_repo()
        self.init()
        os.environ["FAKE_DELEGATE_WRITE"] = "src/tracked.py"
        self.addCleanup(os.environ.pop, "FAKE_DELEGATE_WRITE", None)
        self.dispatch()
        os.environ["FAKE_DELEGATE_WRITE"] = "src/brand_new.py"
        self.dispatch()
        evs = self.state()["tasks"][0]["evidence"]
        fc1 = evs[0]["files_changed"]
        self.assertEqual(fc1["tracked"], ["src/tracked.py"])
        self.assertEqual(fc1["untracked"], [])
        fc2 = evs[1]["files_changed"]
        # Second dispatch's checkpoint captured the first dispatch's edit via
        # stash create; the new file is visible only in the untracked inventory.
        self.assertEqual(fc2["tracked"], [])
        self.assertIn("src/brand_new.py", fc2["untracked"])


# ---------------------------------------------------------------------------
# commit-green
# ---------------------------------------------------------------------------

class TestCommitGreen(CascadeTestCase):
    def test_refused_without_verify_pass(self):
        self.git_init_repo()
        self.init()
        self.dispatch()
        result = self.run_cascade("commit-green", "--workspace", self.ws,
                                  "--task", "t1")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reason"], "verify_not_passed")

    def test_refused_when_not_a_git_repo(self):
        self.init()
        self.dispatch()
        self.assertEqual(self.run_cascade(
            "verify", "--workspace", self.ws, "--task", "t1").returncode, 0)
        result = self.run_cascade("commit-green", "--workspace", self.ws,
                                  "--task", "t1")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reason"], "not_a_git_repo")

    def test_commits_scope_paths_after_verify(self):
        (self.ws / "src" / "tracked.py").write_text("original\n", encoding="utf-8")
        self.git_init_repo()
        self.init()
        os.environ["FAKE_DELEGATE_WRITE"] = "src/tracked.py"
        self.addCleanup(os.environ.pop, "FAKE_DELEGATE_WRITE", None)
        self.dispatch()
        self.assertEqual(self.run_cascade(
            "verify", "--workspace", self.ws, "--task", "t1").returncode, 0)
        result = self.run_cascade("commit-green", "--workspace", self.ws,
                                  "--task", "t1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        out = json.loads(result.stdout)
        self.assertTrue(out["committed"])
        self.assertEqual(out["scope"], ["src"])
        log = self.git("log", "-1", "--format=%s").stdout.strip()
        self.assertEqual(log, "cascade: task t1 verified green")
        self.assertEqual(self.git("status", "--porcelain", "--", "src").stdout, "")
        self.assertEqual(self.state()["tasks"][0]["evidence"][-1]["status"],
                         "commit_green")

    def test_second_commit_green_refused_nothing_to_commit(self):
        (self.ws / "src" / "tracked.py").write_text("original\n", encoding="utf-8")
        self.git_init_repo()
        self.init()
        os.environ["FAKE_DELEGATE_WRITE"] = "src/tracked.py"
        self.addCleanup(os.environ.pop, "FAKE_DELEGATE_WRITE", None)
        self.dispatch()
        self.run_cascade("verify", "--workspace", self.ws, "--task", "t1")
        self.assertEqual(self.run_cascade(
            "commit-green", "--workspace", self.ws, "--task", "t1").returncode, 0)
        result = self.run_cascade("commit-green", "--workspace", self.ws,
                                  "--task", "t1")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reason"], "nothing_to_commit")


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

class TestRollback(CascadeTestCase):
    def test_refused_without_dispatch(self):
        self.git_init_repo()
        self.init()
        result = self.run_cascade("rollback", "--workspace", self.ws, "--task", "t1")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reason"], "no_dispatch_recorded")

    def test_refused_while_native_dispatch_in_flight(self):
        self.git_init_repo()
        self.init()
        self.dispatch(extra=("--escalate",))  # native k3 rung: non-terminal
        result = self.run_cascade("rollback", "--workspace", self.ws, "--task", "t1")
        self.assertEqual(result.returncode, 3)
        out = json.loads(result.stdout)
        self.assertEqual(out["reason"], "last_dispatch_not_terminal")
        self.assertEqual(out["last_status"], "native_dispatch")

    def test_rollback_restores_tracked_scope_and_reports_untracked(self):
        (self.ws / "src" / "tracked.py").write_text("original\n", encoding="utf-8")
        self.git_init_repo()
        self.init()
        os.environ["FAKE_DELEGATE_WRITE"] = "src/tracked.py"
        self.dispatch()
        self.assertEqual((self.ws / "src" / "tracked.py").read_text(),
                         "written by fake delegate\n")
        result = self.run_cascade("rollback", "--workspace", self.ws, "--task", "t1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.ws / "src" / "tracked.py").read_text(), "original\n")
        # Untracked worker files are left in place and reported.
        os.environ["FAKE_DELEGATE_WRITE"] = "src/new_file.py"
        self.addCleanup(os.environ.pop, "FAKE_DELEGATE_WRITE", None)
        self.dispatch()
        result = self.run_cascade("rollback", "--workspace", self.ws, "--task", "t1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        out = json.loads(result.stdout)
        self.assertIn("src/new_file.py", out["untracked_left_in_place"])
        self.assertTrue((self.ws / "src" / "new_file.py").exists())
        self.assertEqual(self.state()["tasks"][0]["evidence"][-1]["status"],
                         "rollback")


# ---------------------------------------------------------------------------
# handoff
# ---------------------------------------------------------------------------

class TestHandoff(CascadeTestCase):
    def test_handoff_packet_contents_and_determinism(self):
        self.init(threat_model="adversarial-local")
        self.dispatch()
        self.run_cascade("record-decision", "--workspace", self.ws,
                         "--decision", "d", "--rationale", "r", "--source", "user")
        r1 = self.run_cascade("handoff", "--workspace", self.ws)
        r2 = self.run_cascade("handoff", "--workspace", self.ws)
        self.assertEqual(r1.returncode, 0)
        self.assertEqual(r1.stdout, r2.stdout)  # deterministic from state
        packet = json.loads(r1.stdout)
        self.assertEqual(packet["goal"], "fixture goal")
        self.assertEqual(packet["threat_model"], "adversarial-local")
        self.assertEqual(packet["tasks"][0]["status"], "in_progress")
        self.assertEqual(packet["tasks"][0]["attempts"], 1)
        self.assertEqual(packet["counters"]["executor_attempts"], 1)
        self.assertEqual(len(packet["decisions"]), 1)
        self.assertTrue(packet["evidence_dir"].endswith("evidence"))
        self.assertLessEqual(len(r1.stdout.encode("utf-8")), 8193)

    def test_handoff_hard_cap_drops_oldest_decisions(self):
        self.init()
        state = self.state()
        state["decisions"] = [{
            "timestamp": "2026-08-13T00:00:00.000+00:00",
            "decision": f"decision {i} " + "x" * 200,
            "rationale": "r", "rejected_alternatives": [], "source": "leader",
        } for i in range(100)]
        self.save_state(state)
        result = self.run_cascade("handoff", "--workspace", self.ws)
        self.assertEqual(result.returncode, 0)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), 8193)
        packet = json.loads(result.stdout)
        self.assertGreater(packet["decisions_dropped_oldest"], 0)
        # Newest decisions survive.
        self.assertIn("decision 99", json.dumps(packet["decisions"]))


# ---------------------------------------------------------------------------
# record-qc dismissal of blocker/major findings (F8)
# ---------------------------------------------------------------------------

class TestDismissFindings(CascadeTestCase):
    BLOCKER = json.dumps([{"severity": "blocker", "location": "src/a.py:1",
                           "claim": "local-tamper theoretical"}])

    def test_accept_with_blocker_requires_dismiss_reason(self):
        self.init()
        result = self.run_cascade("record-qc", "--workspace", self.ws,
                                  "--task", "t1", "--verdict", "accept",
                                  "--findings", self.BLOCKER)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reason"],
                         "open_blocker_major_findings")
        self.assertEqual(self.state().get("dismissed_findings"), [])

    def test_dismiss_stores_finding_verbatim(self):
        self.init(threat_model="single-operator")
        result = self.run_cascade("record-qc", "--workspace", self.ws,
                                  "--task", "t1", "--verdict", "accept",
                                  "--findings", self.BLOCKER,
                                  "--dismiss-reason",
                                  "single-operator threat model: out of scope")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = self.state()
        self.assertEqual(state["tasks"][0]["status"], "done")
        dismissed = state["dismissed_findings"]
        self.assertEqual(len(dismissed), 1)
        self.assertEqual(dismissed[0]["finding"], json.loads(self.BLOCKER)[0])
        self.assertEqual(dismissed[0]["dismiss_reason"],
                         "single-operator threat model: out of scope")
        self.assertEqual(dismissed[0]["threat_model"], "single-operator")
        self.assertEqual(dismissed[0]["task_id"], "t1")
        # Surfaced by status and handoff.
        status = json.loads(self.run_cascade("status", "--workspace", self.ws).stdout)
        self.assertEqual(status["dismissed_findings"], dismissed)
        handoff = json.loads(self.run_cascade("handoff", "--workspace", self.ws).stdout)
        self.assertEqual(handoff["dismissed_findings"], dismissed)


if __name__ == "__main__":
    unittest.main()
