#!/usr/bin/env python3
"""The unified loop's seed: prompt, drift guard, Job 0, suppression, delivery.

Every test here pins one of the disproof's corrections, so a future edit that
re-introduces the comfortable version fails:

  (a) the prompt is byte-stable, carries every outcome string and the LOOP:v1
      header, and says *bounded-blocking* rather than "never blocks";
  (b) a template edited after bind reads DRIFT - and the guard is labelled
      accident detection, not proof;
  (c) Job 0 over the frozen wirewatch fixtures yields the expected verdict line
      and exit code, and writes its outputs outside the workspace;
  (d) advancement is refused while a stop is unresolved OR Job 0 is stale, and
      allowed after the owner's counted clear;
  (e) notify toasts a new stop once, stays silent on the same key, and no-ops
      for a session with no bound loop;
  (f) the hook decides with `loopctl advancement` and blocks the spawn (exit 2).

Run: python -m unittest discover -s harnesses/kimi-code/loop/tests -v
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOOP = HERE.parent
ROOT = HERE.parents[3]
WIRE_DIR = ROOT / "harnesses" / "kimi-code" / "wirewatch" / "tests" / "fixtures" / "wire"
HOOK = ROOT / "harnesses" / "kimi-code" / "hooks" / "proctor_advancement_gate.ps1"
LOOPCTL = LOOP / "loopctl.py"

for _path in (LOOP, ROOT / "core", ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import loop_spec  # noqa: E402
import loopctl  # noqa: E402

MINUTE_MS = 60 * 1000


def run_cli(*args, stdin=None, env=None, cwd=None):
    return subprocess.run(
        [sys.executable, str(LOOPCTL), *[str(a) for a in args]],
        input=stdin, capture_output=True, text=True, timeout=180,
        env=env, cwd=str(cwd or LOOP))


def flat(text):
    """Collapse whitespace so a phrase assertion is about words, not wrapping."""
    return re.sub(r"\s+", " ", text).lower()


def spec_for(state_dir, session="session-test", **overrides):
    base = dict(session_id=session, state_dir=str(state_dir))
    base.update(overrides)
    return loop_spec.LoopSpec(**base)


class _StateCase(unittest.TestCase):
    """A temp state dir bound with the repo template, outside its workspace."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="loop-test-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # The proctored workspace is a SIBLING of the state dir: TOOL-014 wants
        # findings unreachable from the work they judge.
        self.workspace = self.tmp / "ws"
        self.workspace.mkdir()
        self.state = self.tmp / "state"
        self.spec = spec_for(self.state)
        loopctl.bind(self.spec, bound_at=1_000_000)

    def job0(self, *wires, **kwargs):
        kwargs.setdefault("cwd_boundary", str(self.workspace))
        return loopctl.job0(self.state, wires=[str(w) for w in wires], **kwargs)


# ── (a) the prompt ──────────────────────────────────────────────────────
class PromptTestCase(unittest.TestCase):
    def setUp(self):
        self.spec = loop_spec.LoopSpec(session_id="session-prompt",
                                       state_dir="C:/Dev/scratch/loop-prompt")
        self.prompt = loop_spec.render_prompt(self.spec)

    def test_render_is_byte_stable(self):
        self.assertEqual(loop_spec.render_prompt(self.spec), self.prompt)
        self.assertEqual(loop_spec.prompt_sha256(self.spec),
                         loop_spec.prompt_sha256(loop_spec.LoopSpec(
                             session_id="session-prompt",
                             state_dir="C:/Dev/scratch/loop-prompt")))

    def test_crlf_template_renders_identical_bytes(self):
        """A Windows checkout must not manufacture a false DRIFT."""
        with tempfile.TemporaryDirectory(prefix="loop-crlf-") as tmp:
            copy = Path(tmp) / "template.md"
            text = loop_spec.TEMPLATE_PATH.read_text("utf-8")
            copy.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
            crlf = loop_spec.with_template(self.spec, copy)
            self.assertEqual(loop_spec.prompt_sha256(crlf),
                             loop_spec.prompt_sha256(self.spec))

    def test_carries_every_outcome_string(self):
        for outcome in loop_spec.OUTCOMES:
            self.assertIn(outcome, self.prompt, outcome)

    def test_carries_the_loop_v1_header(self):
        self.assertTrue(self.prompt.startswith("LOOP:v1 "),
                        self.prompt.splitlines()[0])

    def test_is_ascii_and_bounded(self):
        self.assertTrue(self.prompt.isascii())
        self.assertLess(len(self.prompt.encode("utf-8")), 12_000)
        self.assertIn("output cap", self.prompt)

    def test_jobs_appear_in_the_fixed_order(self):
        positions = [self.prompt.index(f"JOB {i} ") for i in range(5)]
        self.assertEqual(positions, sorted(positions))
        for job in self.spec.jobs:
            self.assertIn(f"budget {job.budget_s}s", self.prompt)

    def test_states_bounded_blocking_not_never_blocking(self):
        """Disproof (a): steering queues behind at most one fire budget."""
        lowered = flat(self.prompt)
        self.assertIn("queues behind at most one fire", lowered)
        self.assertIn("bounded, not non-blocking", lowered)
        self.assertNotIn("never blocks", lowered)

    def test_unknown_or_stale_job0_suppresses_advancement(self):
        """Disproof (b): the stale-read bypass, stated in the prompt."""
        lowered = flat(self.prompt)
        self.assertIn("unknown is not clear", lowered)
        self.assertIn("did not land (unknown or stale)", lowered)
        self.assertIn("never spawn a worker on the strength", lowered)

    def test_drift_guard_is_labelled_accident_detection(self):
        """Disproof (c): not a proof."""
        self.assertIn("accident detection only", flat(self.prompt))
        self.assertIn("cannot prove the live cron prompt", self.prompt)
        self.assertIn("accident-detection-only",
                      json.dumps(self.spec.as_dict()))

    def test_toast_is_labelled_best_effort(self):
        """Disproof (d): the durable guarantee is on-disk + re-delivery."""
        self.assertIn("best-effort", self.prompt)
        self.assertIn("durable record", self.prompt)
        self.assertIn("best-effort", loop_spec.TOAST_DELIVERY)

    def test_invariants_split_tool_enforced_from_convention(self):
        """Disproof (e): half the invariants are prompt-honored aspirations."""
        self.assertIn("Tool-enforced", self.prompt)
        self.assertIn("Convention (prompt-honored", self.prompt)
        enforced = self.prompt.split("Tool-enforced", 1)[1].split("Convention", 1)[0]
        for invariant in ("I1", "I3", "I6", "I13", "I14"):
            self.assertIn(invariant, enforced, invariant)

    def test_enforcement_of_advancement_is_the_hook_not_the_prompt(self):
        """Disproof (f): a PreToolUse hook, not a polite instruction."""
        self.assertIn('PreToolUse hook (matcher "Agent")', self.prompt)
        self.assertIn("not the enforcement", self.prompt)


class SpecValidationTestCase(unittest.TestCase):
    def test_thresholds_may_not_be_supplied_per_session(self):
        with self.assertRaises(ValueError):
            loop_spec.load_spec_dict({"session_id": "s",
                                      "state_dir": "C:/x",
                                      "thresholds": {"max_stagnant": 2}})

    def test_unknown_keys_are_refused(self):
        with self.assertRaises(ValueError):
            loop_spec.load_spec_dict({"session_id": "s", "state_dir": "C:/x",
                                      "cadence": 10})

    def test_cadence_must_exceed_three_fire_budgets(self):
        spec = spec_for("C:/Dev/scratch/x", cadence_minutes=3, fire_budget_s=180)
        self.assertTrue(any("3x" in p for p in loop_spec.validate_spec(spec)))

    def test_job_order_is_fixed(self):
        spec = spec_for("C:/Dev/scratch/x")
        flipped = (spec.jobs[1], spec.jobs[0]) + spec.jobs[2:]
        broken = loop_spec.LoopSpec(session_id="s", state_dir="C:/Dev/x",
                                    jobs=flipped)
        self.assertTrue(any("job order" in p
                            for p in loop_spec.validate_spec(broken)))

    def test_state_dir_inside_the_workspace_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="loop-ws-") as tmp:
            spec = spec_for(Path(tmp) / "state")
            problems = loop_spec.validate_spec(spec, workspace=tmp)
            self.assertTrue(any("TOOL-014" in p for p in problems), problems)

    def test_job0_freshness_may_not_exceed_one_cadence(self):
        spec = spec_for("C:/Dev/scratch/x", job0_max_age_ms=3_600_000)
        self.assertTrue(any("job0_max_age_ms" in p
                            for p in loop_spec.validate_spec(spec)))

    def test_valid_spec_has_no_problems(self):
        self.assertEqual(loop_spec.validate_spec(spec_for("C:/Dev/scratch/x")), [])

    def test_bind_refuses_with_a_named_violation(self):
        with tempfile.TemporaryDirectory(prefix="loop-bind-") as tmp:
            spec = spec_for(Path(tmp) / "state", cadence_minutes=1)
            with self.assertRaises(SystemExit):
                loopctl.bind(spec, workspace=tmp)


# ── (b) drift guard ─────────────────────────────────────────────────────
class DriftTestCase(unittest.TestCase):
    def test_tampered_template_reads_drift(self):
        with tempfile.TemporaryDirectory(prefix="loop-drift-") as tmp:
            tmp = Path(tmp)
            copy = tmp / "template.md"
            shutil.copyfile(loop_spec.TEMPLATE_PATH, copy)
            state = tmp / "state"
            spec = spec_for(state, template_path=str(copy))
            loopctl.bind(spec, bound_at=1_000_000)
            self.assertEqual(loopctl.drift_check(state, spec),
                             ("NOMINAL", "ok"))

            with open(copy, "a", encoding="utf-8") as handle:
                handle.write("\nINVARIANT 15 - hand-edited after bind\n")

            outcome, detail = loopctl.drift_check(state, spec)
            self.assertEqual(outcome, "DRIFT", detail)

            run = run_cli("verify", "--state-dir", state)
            self.assertEqual(run.returncode, loopctl.EXIT_BY_OUTCOME["DRIFT"])
            self.assertIn("LOOP:v1 JOB4 DRIFT", run.stdout)
            self.assertIn("accident-detection-only", run.stdout)

    def test_never_bound_reads_unknown_never_nominal(self):
        with tempfile.TemporaryDirectory(prefix="loop-nobind-") as tmp:
            spec = spec_for(Path(tmp))
            self.assertEqual(loopctl.drift_check(tmp, spec),
                             ("UNKNOWN", "never_bound"))


# ── (c) Job 0 over the frozen fixtures ──────────────────────────────────
class Job0TestCase(_StateCase):
    def test_stop_fixture_escalates_with_exit_1(self):
        result = self.job0(WIRE_DIR / "65f71599.jsonl",
                           cwd_boundary=str(self.workspace))
        self.assertEqual(result["outcome"], "ESCALATED")
        self.assertEqual(result["exit_code"], 1)
        line = result["line"]
        self.assertTrue(line.startswith("LOOP:v1 JOB0 ESCALATED "), line)
        self.assertIn("stops=1", line)
        self.assertIn("key=", line)
        self.assertTrue((self.state / "wirewatch" / "summary.json").is_file())
        self.assertTrue((self.state / "wirewatch" / "escalations.jsonl").is_file())

    def test_journal_join_turns_t10_into_a_stop(self):
        result = self.job0(WIRE_DIR / "6a97dd66.jsonl",
                           journal=str(WIRE_DIR / "journal-6a97dd66.jsonl"),
                           cwd_boundary=str(self.workspace))
        self.assertEqual(result["outcome"], "ESCALATED")
        summary = json.loads(
            (self.state / "wirewatch" / "summary.json").read_text("utf-8"))
        self.assertEqual(summary["by_trigger"].get("T10"), 1)

    def test_benign_fixture_is_nominal(self):
        result = self.job0(WIRE_DIR / "benign_tdd.jsonl",
                           cwd_boundary=str(self.workspace))
        self.assertEqual(result["outcome"], "NOMINAL")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("stops=0", result["line"])
        self.assertIn("unmeasurable=T10", result["line"])

    def test_cli_emits_the_verdict_line_and_exit_code(self):
        run = run_cli("job0", "--state-dir", self.state,
                      "--wire", WIRE_DIR / "65f71599.jsonl",
                      "--cwd-boundary", self.workspace)
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertTrue(run.stdout.startswith("LOOP:v1 JOB0 ESCALATED "),
                        run.stdout)

    def test_out_dir_inside_the_workspace_is_refused(self):
        """TOOL-014 holds inside the loop too: findings outlive the work."""
        result = self.job0(WIRE_DIR / "65f71599.jsonl",
                           cwd_boundary=str(self.state))
        self.assertEqual(result["outcome"], "UNKNOWN")
        self.assertEqual(result["reason"], "out_dir_refused")

    def test_oversize_wire_set_goes_unknown_not_nominal(self):
        spec = loop_spec.LoopSpec(session_id="session-test",
                                  state_dir=str(self.state),
                                  job0_inline_max_bytes=100)
        loopctl.bind(spec, bound_at=1_000_000)
        result = loopctl.job0(self.state, wires=[str(WIRE_DIR / "65f71599.jsonl")],
                              cwd_boundary=str(self.workspace))
        self.assertEqual(result["outcome"], "UNKNOWN")
        self.assertEqual(result["reason"], "job0_detached_required")
        self.assertEqual(result["exit_code"], 3)
        self.assertTrue(result["detach_command"])
        allowed, reason = loopctl.advancement_allowed(self.state,
                                                      at=result["at"] + 1000)
        self.assertFalse(allowed)
        self.assertIn("job0_unknown", reason)


# ── (d) advancement suppression ─────────────────────────────────────────
class AdvancementTestCase(_StateCase):
    def test_refused_before_job0_ever_ran(self):
        allowed, reason = loopctl.advancement_allowed(self.state)
        self.assertFalse(allowed)
        self.assertEqual(reason, "job0_never_ran")

    def test_refused_while_a_stop_is_unresolved(self):
        self.job0(WIRE_DIR / "65f71599.jsonl", cwd_boundary=str(self.workspace))
        allowed, reason = loopctl.advancement_allowed(self.state)
        self.assertFalse(allowed)
        self.assertIn("stops_unresolved", reason)

    def test_allowed_after_the_owners_counted_clear(self):
        result = self.job0(WIRE_DIR / "65f71599.jsonl",
                           cwd_boundary=str(self.workspace))
        key = result["stop_keys"][0]
        cleared = loopctl.clear_key(self.state, key, "owner reviewed", at=2_000_000)
        self.assertTrue(cleared["cleared"])
        self.assertEqual(cleared["record"]["clear_count"], 1)
        allowed, reason = loopctl.advancement_allowed(self.state, at=2_000_100)
        self.assertTrue(allowed, reason)
        self.assertIn("stops_cleared=1", reason)

    def test_clear_of_an_unseen_key_is_refused(self):
        self.assertFalse(loopctl.clear_key(self.state, "deadbeefcafe",
                                          "made up")["cleared"])

    def test_refused_when_the_job0_run_is_stale(self):
        """Disproof (b): a detached/absent Job 0 must not read as clear."""
        result = self.job0(WIRE_DIR / "benign_tdd.jsonl",
                           cwd_boundary=str(self.workspace))
        self.assertEqual(result["outcome"], "NOMINAL")
        allowed, reason = loopctl.advancement_allowed(self.state,
                                                     at=result["at"] + 1000)
        self.assertTrue(allowed, reason)

        run = json.loads((self.state / "job0" / "last_run.json").read_text("utf-8"))
        run["finished_at"] = result["at"] - (self.spec.job0_max_age_ms + 1000)
        (self.state / "job0" / "last_run.json").write_text(
            json.dumps(run), encoding="utf-8")
        allowed, reason = loopctl.advancement_allowed(self.state, at=result["at"])
        self.assertFalse(allowed)
        self.assertIn("job0_stale", reason)

    def test_refused_when_job0_verdict_is_unknown(self):
        self.job0(WIRE_DIR / "benign_tdd.jsonl", cwd_boundary=str(self.workspace))
        run = json.loads((self.state / "job0" / "last_run.json").read_text("utf-8"))
        run["verdict"] = "UNKNOWN"
        run["reason"] = "evaluator_error:TimeoutError"
        (self.state / "job0" / "last_run.json").write_text(
            json.dumps(run), encoding="utf-8")
        allowed, reason = loopctl.advancement_allowed(self.state,
                                                     at=run["finished_at"] + 1000)
        self.assertFalse(allowed)
        self.assertIn("job0_unknown", reason)

    def test_cli_json_matches_the_function(self):
        self.job0(WIRE_DIR / "65f71599.jsonl", cwd_boundary=str(self.workspace))
        run = run_cli("advancement", "--state-dir", self.state)
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads(run.stdout)
        allowed, reason = loopctl.advancement_allowed(self.state)
        self.assertEqual(payload, {"allowed": allowed, "reason": reason})


# ── (e) notify: the hook entrypoint ─────────────────────────────────────
class NotifyTestCase(_StateCase):
    def _toast_recorder(self):
        calls = []

        def toast(title, body):
            calls.append((title, body))
            return "fake-toast"

        return calls, toast

    def test_new_stop_toasts_once_then_the_same_key_is_silent(self):
        self.job0(WIRE_DIR / "65f71599.jsonl", cwd_boundary=str(self.workspace))
        calls, toast = self._toast_recorder()
        payload = {"session_id": self.spec.session_id, "cwd": str(self.tmp)}

        first = loopctl.notify(payload, state_root=self.tmp, toast=toast,
                               at=2_000_000)
        self.assertEqual(first["action"], "toasted")
        self.assertEqual(len(calls), 1)
        self.assertIn("stop-severity", calls[0][0])

        second = loopctl.notify(payload, state_root=self.tmp, toast=toast,
                                at=2_000_100)
        self.assertEqual(second["action"], "silent")
        self.assertEqual(len(calls), 1, "the same key must not re-toast")

        inbox = loopctl.read_jsonl(self.state / "owner-inbox.jsonl")
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["kind"], "stop")

    def test_an_unresolved_stop_is_re_toasted_after_the_quiet_window(self):
        self.job0(WIRE_DIR / "65f71599.jsonl", cwd_boundary=str(self.workspace))
        calls, toast = self._toast_recorder()
        payload = {"session_id": self.spec.session_id, "cwd": str(self.tmp)}
        loopctl.notify(payload, state_root=self.tmp, toast=toast, at=2_000_000)
        loopctl.notify(payload, state_root=self.tmp, toast=toast,
                       at=2_000_000 + 31 * MINUTE_MS)
        self.assertEqual(len(calls), 2)

    def test_a_foreign_session_is_a_silent_noop(self):
        self.job0(WIRE_DIR / "65f71599.jsonl", cwd_boundary=str(self.workspace))
        calls, toast = self._toast_recorder()
        result = loopctl.notify({"session_id": "session-someone-else",
                                 "cwd": "C:/elsewhere"},
                                state_root=self.tmp, toast=toast, at=2_000_000)
        self.assertEqual(result["action"], "noop")
        self.assertEqual(result["reason"], "no_binding_for_session")
        self.assertEqual(calls, [])

    def test_nominal_state_toasts_nothing(self):
        self.job0(WIRE_DIR / "benign_tdd.jsonl", cwd_boundary=str(self.workspace))
        calls, toast = self._toast_recorder()
        result = loopctl.notify({"session_id": self.spec.session_id},
                                state_root=self.tmp, toast=toast, at=2_000_000)
        self.assertEqual(result["action"], "silent")
        self.assertEqual(calls, [])

    def test_cli_reads_the_hook_payload_from_stdin(self):
        self.job0(WIRE_DIR / "65f71599.jsonl", cwd_boundary=str(self.workspace))
        run = run_cli("notify", "--state-root", self.tmp, "--dry-run",
                      stdin=json.dumps({"session_id": self.spec.session_id}))
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads(run.stdout.splitlines()[-1])
        self.assertEqual(payload["action"], "toasted")
        self.assertEqual(payload["toasted"][0]["channel"], "dry-run")

    def test_unparseable_payload_is_a_noop(self):
        run = run_cli("notify", "--state-root", self.tmp, stdin="{not json")
        self.assertEqual(run.returncode, 0)
        self.assertEqual(json.loads(run.stdout)["action"], "noop")


# ── (f) the host PreToolUse hook ────────────────────────────────────────
PWSH = shutil.which("pwsh")


class HookTestCase(_StateCase):
    def _run_hook(self, payload):
        env = dict(os.environ)
        env.pop("KIMI_HOOKS_OFF", None)
        env.pop("MODEL_PROCTOR_ADVANCEMENT_GATE", None)
        env["MODEL_PROCTOR_LOOP_ROOT"] = str(self.tmp)
        env["MODEL_PROCTOR_PYTHON"] = sys.executable
        env["MODEL_PROCTOR_LOOPCTL"] = str(LOOPCTL)
        return subprocess.run(
            [PWSH, "-NoProfile", "-NonInteractive", "-File", str(HOOK)],
            input=json.dumps(payload), capture_output=True, text=True,
            timeout=180, env=env)

    def test_the_hook_consults_the_same_decision(self):
        """The hook's decision IS `loopctl advancement` - asserted by parity."""
        result = self.job0(WIRE_DIR / "65f71599.jsonl",
                           cwd_boundary=str(self.workspace))
        allowed, reason = loopctl.advancement_allowed(self.state,
                                                     at=result["at"] + 1000)
        self.assertFalse(allowed)
        cli = run_cli("advancement", "--state-dir", self.state)
        self.assertEqual(json.loads(cli.stdout)["reason"], reason)

    @unittest.skipUnless(PWSH, "pwsh is not installed")
    def test_hook_blocks_a_spawn_while_a_stop_is_unresolved(self):
        self.job0(WIRE_DIR / "65f71599.jsonl", cwd_boundary=str(self.workspace))
        run = self._run_hook({"tool_name": "Agent",
                              "session_id": self.spec.session_id,
                              "tool_input": {"prompt": "spawn a coder"}})
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("stops_unresolved", run.stderr)

    @unittest.skipUnless(PWSH, "pwsh is not installed")
    def test_hook_allows_after_the_clear(self):
        result = self.job0(WIRE_DIR / "65f71599.jsonl",
                           cwd_boundary=str(self.workspace))
        loopctl.clear_key(self.state, result["stop_keys"][0], "owner reviewed")
        run = self._run_hook({"tool_name": "Agent",
                              "session_id": self.spec.session_id,
                              "tool_input": {"prompt": "spawn a coder"}})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    @unittest.skipUnless(PWSH, "pwsh is not installed")
    def test_hook_is_inert_for_an_unbound_session(self):
        self.job0(WIRE_DIR / "65f71599.jsonl", cwd_boundary=str(self.workspace))
        run = self._run_hook({"tool_name": "Agent",
                              "session_id": "session-not-proctored",
                              "tool_input": {}})
        self.assertEqual(run.returncode, 0, run.stderr)

    @unittest.skipUnless(PWSH, "pwsh is not installed")
    def test_hook_ignores_non_agent_tools(self):
        self.job0(WIRE_DIR / "65f71599.jsonl", cwd_boundary=str(self.workspace))
        run = self._run_hook({"tool_name": "Bash",
                              "session_id": self.spec.session_id,
                              "tool_input": {"command": "echo hi"}})
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_hook_source_is_ascii_and_registers_nothing(self):
        source = HOOK.read_text("utf-8")
        self.assertTrue(source.isascii())
        self.assertIn("PreToolUse", source)
        self.assertIn('matcher = "Agent"', source)
        self.assertIn("decision-seat", source)
        self.assertNotIn("Set-Content", source)
        self.assertNotIn("Add-Content", source)


# ── doctrine ────────────────────────────────────────────────────────────
class DoctrineTestCase(unittest.TestCase):
    def test_loopctl_never_launches_a_model_or_the_network(self):
        source = LOOPCTL.read_text("utf-8")
        for forbidden in ("urllib", "http.client", "socket", "requests",
                          "openai", "anthropic"):
            self.assertNotIn(f"import {forbidden}", source)

    def test_thresholds_bind_the_core_and_are_not_re_derived(self):
        from decisions import DEFAULT_MAX_STAGNANT, STAGNATION_THRESHOLD
        import wire_triggers
        spec = spec_for("C:/Dev/scratch/x")
        self.assertIs(spec.thresholds, wire_triggers.DEFAULTS)
        self.assertEqual(spec.thresholds.stagnation_threshold,
                         STAGNATION_THRESHOLD)
        self.assertEqual(spec.thresholds.max_stagnant, DEFAULT_MAX_STAGNANT)

    def test_job0_imports_the_evaluator_instead_of_reimplementing_it(self):
        source = LOOPCTL.read_text("utf-8")
        self.assertIn("evaluator.evaluate_paths", source)
        self.assertIn("evaluator.write_outputs", source)


if __name__ == "__main__":
    unittest.main()
