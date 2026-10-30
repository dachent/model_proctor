#!/usr/bin/env python3
"""Smoke: the escalation evaluator fires on the audited incidents, and only on
them.

The three fixtures are reduced, deterministic wires synthesized from
``review/escalation-audit-20261029.md`` (see ``fixtures/gen_fixtures.py``). The
durability doctrine forbids depending on the live ``C:\\`` wires, so these files
are the regression; the one-time live confirmation is reported in the handoff,
not asserted here.

Two directions, both required:
  - every named incident fires the trigger it is the evidence for, at the
    timestamp the incident happened;
  - benign look-alikes (a 5-edit refactor with distinct outcomes, a long poll
    with changing output, a TDD red-edit-green) fire nothing. That is the
    anti-overfitting control: a trigger that cannot tell circling from
    ordinary work is not evidence.

Plus the contract surface: exit 1 only on stop-severity findings, byte-
identical output across runs, TOOL-014 refusal of an in-workspace output dir,
and unmeasurable triggers reported by name rather than passing silently.

Run: python -m unittest discover -s harnesses/kimi-code/wirewatch/tests -v
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALUATOR = HERE.parent / "evaluator.py"
ROOT = HERE.parents[3]
WIRE_DIR = HERE / "fixtures" / "wire"

sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "scripts"))
import extract_log  # noqa: E402
import wire_triggers  # noqa: E402

# Fixture clocks (see gen_fixtures.py) — the audited sessions' own bases.
S1 = 1793234880000
S2 = 1793032946000
S3 = 1793118598000
MINUTE = 60 * 1000

# (fixture, trigger, severity, expected epoch ms, tolerance)
EXPECTED_FIRINGS = [
    # 65f71599: the bb.ps1 404 trio, the 7x-in-3-min test edit, the 47-minute
    # turn that ended on a re-dispatch, and the nondiscriminating green.
    ("65f71599.jsonl", "T5", "lane_switch", S1 + 700000 + 41050, 2 * MINUTE),
    ("65f71599.jsonl", "T6", "lane_switch", S1 + 700000 + 106025, 5 * MINUTE),
    ("65f71599.jsonl", "T9", "lane_switch", S1 + 700000 + 2820000, 5 * MINUTE),
    ("65f71599.jsonl", "T2", "stop", S1 + 700000 + 3000000 + 2000, 5 * MINUTE),
    # 6a97dd66: the repeated owner prompt, the branch generations, the 4-hour
    # turn.
    ("6a97dd66.jsonl", "T14", "lane_switch", S2 + 700000 + 2000, 2 * MINUTE),
    ("6a97dd66.jsonl", "T8", "lane_switch", S2 + 1400000 + 100050, 5 * MINUTE),
    ("6a97dd66.jsonl", "T9", "lane_switch", S2 + 2400000 + 14678616, 5 * MINUTE),
    # bbad08a0: the pyenv shim failing identically, the 42-second edit burst,
    # the 62- and 118-minute sleep turns, the repeated owner prompt.
    ("bbad08a0.jsonl", "T5", "lane_switch", S3 + 10000 + 2 * 900000 + 50,
     2 * MINUTE),
    ("bbad08a0.jsonl", "T6", "lane_switch", S3 + 3800000 + 7 * 5000 + 1100,
     2 * MINUTE),
    ("bbad08a0.jsonl", "T9", "lane_switch", S3 + 5000000 + 4390110, 5 * MINUTE),
    ("bbad08a0.jsonl", "T9", "lane_switch", S3 + 10900000 + 8448936,
     5 * MINUTE),
    ("bbad08a0.jsonl", "T14", "lane_switch", S3 + 10200000 - 1000, 2 * MINUTE),
]

BENIGN = ("benign_refactor.jsonl", "benign_poll.jsonl", "benign_tdd.jsonl")


def evaluate_file(name, journal=None):
    facts, coverage = extract_log.extract_file(WIRE_DIR / name)
    return facts, coverage, wire_triggers.evaluate(facts, journal=journal)


def run_cli(*args, cwd):
    return subprocess.run(
        [sys.executable, str(EVALUATOR), *[str(a) for a in args]],
        capture_output=True, text=True, cwd=str(cwd), timeout=180)


class IncidentFiringTestCase(unittest.TestCase):
    """(b) The evaluator fires at each named incident."""

    def test_named_incidents_fire_at_the_incident(self):
        for name, trigger, severity, expected_at, tolerance in EXPECTED_FIRINGS:
            with self.subTest(fixture=name, trigger=trigger, at=expected_at):
                facts, coverage, findings = evaluate_file(name)
                self.assertEqual(coverage["records_unrecognized"], 0,
                                 coverage["unrecognized_types"])
                rows = [f for f in findings
                        if f["trigger"] == trigger and f["severity"] == severity]
                self.assertTrue(rows, f"{trigger}/{severity} did not fire on "
                                      f"{name}: {[f['trigger'] for f in findings]}")
                near = [f for f in rows
                        if isinstance(f["at"], (int, float))
                        and abs(f["at"] - expected_at) <= tolerance]
                self.assertTrue(
                    near,
                    f"{trigger} fired at {[f['at'] for f in rows]}, expected "
                    f"~{expected_at} (+-{tolerance}ms)")

    def test_every_incident_fixture_has_its_expected_trigger_set(self):
        expected = {
            "65f71599.jsonl": {"T2", "T5", "T6", "T9"},
            "6a97dd66.jsonl": {"T8", "T9", "T14"},
            "bbad08a0.jsonl": {"T5", "T6", "T9", "T14"},
        }
        for name, triggers in expected.items():
            with self.subTest(fixture=name):
                _, _, findings = evaluate_file(name)
                fired = {f["trigger"] for f in findings
                         if f["severity"] != "unmeasurable"}
                self.assertEqual(fired, triggers)


class AntiOverfittingTestCase(unittest.TestCase):
    """(c) Benign look-alikes must not fire."""

    def test_benign_lookalikes_do_not_fire(self):
        for name in BENIGN:
            with self.subTest(fixture=name):
                _, coverage, findings = evaluate_file(name)
                self.assertEqual(coverage["records_unrecognized"], 0)
                fired = [f for f in findings
                         if f["severity"] != "unmeasurable"]
                self.assertEqual(fired, [], name)

    def test_benign_unmeasurable_is_reported_not_hidden(self):
        """No journal means T10 has no estimate. That is unmeasurable, and it
        is named — the one thing it must never be is a silent pass."""
        _, _, findings = evaluate_file("benign_tdd.jsonl")
        unm = [f for f in findings if f["severity"] == "unmeasurable"]
        self.assertEqual([f["trigger"] for f in unm], ["T10"])
        self.assertIn("journal", unm[0]["evidence"]["reason"])

    def test_tdd_red_edits_do_not_fire_t6(self):
        """Disproof R6: editing a test while the verifier is red is TDD, not
        circling. The fixture has 9 such edits and must stay quiet."""
        facts, _, findings = evaluate_file("benign_tdd.jsonl")
        self.assertEqual(len(facts["file_mutations"]), 9)
        self.assertFalse([f for f in findings if f["trigger"] == "T6"])


class CliContractTestCase(unittest.TestCase):
    """The command-line surface: outputs, exit codes, TOOL-014."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wirewatch-test-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # The "workspace" is the cwd; findings must land outside it.
        self.workspace = self.tmp / "ws"
        self.workspace.mkdir()
        self.out = self.tmp / "state"

    def test_writes_escalations_and_summary_and_exits_1_on_stop(self):
        run = run_cli(WIRE_DIR / "65f71599.jsonl", "--out", self.out,
                      cwd=self.workspace)
        self.assertEqual(run.returncode, 1, run.stderr + run.stdout)
        summary = json.loads((self.out / "summary.json").read_text("utf-8"))
        self.assertGreaterEqual(summary["stops"], 1)
        self.assertIn("T2", summary["by_trigger"])
        lines = (self.out / "escalations.jsonl").read_text(
            "utf-8").splitlines()
        self.assertTrue(lines)
        for line in lines:
            row = json.loads(line)
            self.assertEqual(sorted(row), ["at", "evidence", "severity",
                                           "trigger", "wire"])

    def test_benign_exits_0_with_no_escalations(self):
        run = run_cli(*[WIRE_DIR / n for n in BENIGN], "--out", self.out,
                      cwd=self.workspace)
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertEqual(
            (self.out / "escalations.jsonl").read_text("utf-8"), "")
        summary = json.loads((self.out / "summary.json").read_text("utf-8"))
        self.assertEqual(summary["findings"], 0)
        self.assertTrue(summary["unmeasurable"])

    def test_output_is_byte_identical_across_runs(self):
        first, second = self.tmp / "a", self.tmp / "b"
        for out in (first, second):
            run = run_cli(WIRE_DIR / "bbad08a0.jsonl", "--out", out,
                          cwd=self.workspace)
            self.assertEqual(run.returncode, 0, run.stdout)
        for name in ("escalations.jsonl", "summary.json"):
            self.assertEqual((first / name).read_bytes(),
                             (second / name).read_bytes(), name)

    def test_refuses_out_inside_workspace(self):
        run = run_cli(WIRE_DIR / "65f71599.jsonl", "--out",
                      self.workspace / "findings", cwd=self.workspace)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("out_dir_inside_workspace", run.stderr + run.stdout)

    def test_refuses_out_inside_the_session_dir(self):
        run = run_cli(WIRE_DIR / "65f71599.jsonl", "--out", WIRE_DIR / "state",
                      cwd=self.workspace)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("out_dir_inside_session", run.stderr + run.stdout)

    def test_journal_join_turns_t10_unmeasurable_into_a_stop(self):
        without = self.tmp / "without"
        run = run_cli(WIRE_DIR / "6a97dd66.jsonl", "--out", without,
                      cwd=self.workspace)
        self.assertEqual(run.returncode, 0, run.stdout)
        summary = json.loads((without / "summary.json").read_text("utf-8"))
        self.assertIn("T10", [u["trigger"] for u in summary["unmeasurable"]])
        self.assertNotIn("T10", summary["by_trigger"])

        with_journal = self.tmp / "with"
        run = run_cli(WIRE_DIR / "6a97dd66.jsonl", "--journal",
                      WIRE_DIR / "journal-6a97dd66.jsonl",
                      "--out", with_journal, cwd=self.workspace)
        self.assertEqual(run.returncode, 1, run.stdout)
        summary = json.loads((with_journal / "summary.json").read_text("utf-8"))
        self.assertEqual(summary["by_trigger"].get("T10"), 1)
        self.assertNotIn("T10", [u["trigger"] for u in summary["unmeasurable"]])

    def test_missing_wire_is_an_error_not_an_empty_pass(self):
        run = run_cli(self.tmp / "nope.jsonl", "--out", self.out,
                      cwd=self.workspace)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("wire_not_a_file", run.stderr + run.stdout)


class CrossWireTestCase(unittest.TestCase):
    """Branch generations are a repository fact, not an agent fact."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wirewatch-xwire-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.workspace = self.tmp / "ws"
        self.workspace.mkdir()

    def _wire(self, name, branches):
        rows = [{"type": "metadata", "protocol_version": "1.4", "time": 1}]
        for i, branch in enumerate(branches):
            rows.append({
                "type": "context.append_loop_event", "agentId": "main",
                "event": {"type": "tool.call", "name": "Bash",
                          "turnId": str(i),
                          "args": {"command": f"git checkout -b {branch}"},
                          "toolCallId": f"c{i}"},
                "time": 10 + i})
        path = self.tmp / "sess" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r, sort_keys=True)
                                  for r in rows) + "\n", encoding="utf-8")
        return path

    def test_session_aggregation_is_what_fires_t8(self):
        first = self._wire("a.jsonl", ["feat/WKFINOPS-77-one",
                                       "feat/WKFINOPS-77-two"])
        second = self._wire("b.jsonl", ["feat/WKFINOPS-77-three",
                                        "feat/WKFINOPS-77-four"])
        # Per wire, two branches on the ticket: below the fire line.
        out = self.tmp / "one"
        run = run_cli(first, "--out", out, cwd=self.workspace)
        self.assertEqual(run.returncode, 0, run.stdout)
        summary = json.loads((out / "summary.json").read_text("utf-8"))
        self.assertNotIn("T8", summary["by_trigger"])

        # Together, four: the session created four generations for one ticket.
        out = self.tmp / "both"
        run = run_cli(first, second, "--out", out, cwd=self.workspace)
        self.assertEqual(run.returncode, 0, run.stdout)
        summary = json.loads((out / "summary.json").read_text("utf-8"))
        self.assertEqual(summary["by_trigger"].get("T8"), 1)
        rows = [json.loads(line) for line in
                (out / "escalations.jsonl").read_text("utf-8").splitlines()]
        t8 = [r for r in rows if r["trigger"] == "T8"][0]
        self.assertEqual(t8["evidence"]["ticket"], "WKFINOPS-77")
        self.assertEqual(t8["evidence"]["branches"], 4)
        self.assertEqual(t8["wire"], "<session>")


class DoctrineTestCase(unittest.TestCase):
    """No LLM in the loop, and no worker-authored value decides non-firing."""

    def test_core_is_pure_and_stdlib_only(self):
        source = (ROOT / "core" / "wire_triggers.py").read_text("utf-8")
        for forbidden in ("subprocess", "urllib", "http.client", "socket",
                          "requests", "openai"):
            self.assertNotIn(f"import {forbidden}", source)
        # The only non-stdlib import is the shared decision core.
        imports = re.findall(r"^(?:import|from) (\w+)", source, re.M)
        self.assertEqual(set(imports) - {"__future__", "json", "os", "re",
                                         "sys", "dataclasses", "annotations"},
                         {"decisions"}, imports)

    def test_thresholds_bind_the_shared_core(self):
        from decisions import DEFAULT_MAX_STAGNANT, STAGNATION_THRESHOLD
        self.assertEqual(wire_triggers.DEFAULTS.stagnation_threshold,
                         STAGNATION_THRESHOLD)
        self.assertEqual(wire_triggers.DEFAULTS.max_stagnant,
                         DEFAULT_MAX_STAGNANT)


if __name__ == "__main__":
    unittest.main()
