"""Scorecard measurement-integrity tests.

Three failure classes these catch, all of them present in the scorecard the
harness originally shipped:

1. A token count printed in the cost column — `cost_per_success` was
   `sum(est_tokens) / successes`, so 201.50 "cost" was 201.5 tokens.
2. A verdict emitted before its guardrail was computed, so `adopted` could be
   true while the simple_fix guardrail said FAIL eight lines later.
3. A silent fallback to the token proxy when metered USD was unavailable.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVALS_DIR))

import report  # noqa: E402

# Config-name -> blind letter, the direction the key file uses; run rows carry
# the letter in `config`.
BLINDING = {"Config1": "D", "Config2": "A", "Config3": "C"}


def row(case_id, category, set_name, config, rep, *, wall, tokens, accept=True):
    return {
        "case_id": case_id,
        "category": category,
        "set": set_name,
        "config": config,
        "rep": rep,
        "error": None,
        "wall_clock_s": wall,
        "agent_exit": 0,
        "timed_out": False,
        "est_tokens": tokens,
        "tokens_reported": None,
        "acceptance_pass": accept,
        "hidden_pass": accept,
        "invariants_pass": accept,
    }


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def metered_rows(rows, costs):
    out = []
    for r, cost in zip(rows, costs):
        m = dict(r)
        m["metered"] = cost is not None
        m["sessions_matched"] = 1 if cost is not None else 0
        m["tokens_by_model"] = {}
        m["cost_usd_by_model"] = {}
        m["total_cost_usd"] = cost
        out.append(m)
    return out


def build(tmpdir, rows, costs, blinding=BLINDING):
    """Write fixtures into tmpdir and return the generated scorecard text."""
    tmpdir = Path(tmpdir)
    write_jsonl(tmpdir / "results.jsonl", rows)
    if costs is not None:
        write_jsonl(tmpdir / "results-metered.jsonl", metered_rows(rows, costs))
    results = report.load_results(str(tmpdir / "results.jsonl"))
    index, present = report.load_metered_index(str(tmpdir / "results-metered.jsonl"))
    info = report.join_costs(results, index, present)
    return report.generate_scorecard(results, blinding, info)


def section(markdown, title):
    out, inside = [], False
    for line in markdown.splitlines():
        if line.startswith("## "):
            inside = line.strip() == f"## {title}"
            continue
        if inside:
            out.append(line)
    return out


def cells_of(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def column(lines, name):
    for line in lines:
        if line.startswith("|") and name in cells_of(line):
            return cells_of(line).index(name)
    raise AssertionError(f"column {name!r} not found")


def table_row(lines, first_cell):
    for line in lines:
        if line.startswith("|"):
            cells = cells_of(line)
            if cells and cells[0] == first_cell:
                return cells
    return None


HOLDOUT = "Aggregate — holdout set"

# Candidate is one third the cost of the baseline and equal on success, and it
# passes the guardrail: everything a token proxy would also call "cheaper".
CHEAPER_CAND = [
    row("h1_case", "mechanical", "holdout", "A", 1, wall=40.0, tokens=4000),
    row("h1_case", "mechanical", "holdout", "C", 1, wall=30.0, tokens=4000),
    row("sf1_case", "simple_fix", "tune", "A", 1, wall=10.0, tokens=1000),
    row("sf1_case", "simple_fix", "tune", "C", 1, wall=9.0, tokens=1000),
]
CHEAPER_COSTS = [0.40, 0.10, 0.01, 0.01]

# Same as above, except the candidate is slower on the simple_fix case.
SLOW_CAND = [
    row("h1_case", "mechanical", "holdout", "A", 1, wall=40.0, tokens=4000),
    row("h1_case", "mechanical", "holdout", "C", 1, wall=30.0, tokens=4000),
    row("sf1_case", "simple_fix", "tune", "A", 1, wall=10.0, tokens=1000),
    row("sf1_case", "simple_fix", "tune", "C", 1, wall=12.0, tokens=1000),
]


class MeteredDollarsNotTokens(unittest.TestCase):
    def test_cost_per_success_is_metered_usd_not_a_token_count(self):
        with tempfile.TemporaryDirectory() as td:
            md = build(td, CHEAPER_CAND, CHEAPER_COSTS)
        holdout = section(md, HOLDOUT)
        cost_col = column(holdout, "Cost/Succ ($)")

        cand = table_row(holdout, "Config3")
        base = table_row(holdout, "Config2")
        self.assertEqual(cand[cost_col], "$0.1000")
        self.assertEqual(base[cost_col], "$0.4000")

        # Both arms report 4000 est_tokens, so the old token proxy would have
        # printed 4000.00 for each of them; a dollar figure is not that.
        self.assertTrue(cand[cost_col].startswith("$"))
        self.assertNotEqual(cand[cost_col], "4000.00")
        self.assertNotIn("4000.00", md)

        src_col = column(holdout, "Cost src")
        self.assertEqual(cand[src_col], "metered")
        self.assertEqual(base[src_col], "metered")
        self.assertIn('cost_source: "metered"', md)

    def test_verdict_passes_only_when_all_three_gates_pass(self):
        with tempfile.TemporaryDirectory() as td:
            md = build(td, CHEAPER_CAND, CHEAPER_COSTS)
        self.assertIn("**Verdict: ADOPTED** Config3 (all gates pass)", md)


class GuardrailGatesTheVerdict(unittest.TestCase):
    def test_adopted_is_false_when_the_guardrail_fails(self):
        with tempfile.TemporaryDirectory() as td:
            md = build(td, SLOW_CAND, CHEAPER_COSTS)
        verdict = section(md, "Adoption Verdict (holdout only)")
        text = "\n".join(verdict)
        # Success and cost both pass, and cost alone would have adopted.
        self.assertIn("success rate: 1.00 vs", text)
        self.assertIn("$0.1000 vs", text)
        self.assertIn("→ < ✓", text)
        self.assertIn("Guardrail (simple_fix wall-clock): FAIL ✗", text)
        self.assertIn("**Verdict: NOT ADOPTED** Config3 (failed: guardrail)", text)

    def test_guardrail_is_reported_before_the_verdict(self):
        with tempfile.TemporaryDirectory() as td:
            md = build(td, SLOW_CAND, CHEAPER_COSTS)
        self.assertIn("**Guardrail: FAIL**", md)
        self.assertLess(md.index("**Guardrail: FAIL**"), md.index("## Adoption Verdict"))


class UnmeteredNeverPrintsTokens(unittest.TestCase):
    def test_absent_metered_file_marks_cost_unknown(self):
        # CHEAPER_CAND passes the success and guardrail gates, so cost is the
        # only reason this verdict can be refused.
        with tempfile.TemporaryDirectory() as td:
            md = build(td, CHEAPER_CAND, costs=None)
        self.assertIn('cost_source: "unmetered"', md)
        holdout = section(md, HOLDOUT)
        cost_col = column(holdout, "Cost/Succ ($)")
        for label in ("Config2", "Config3"):
            self.assertEqual(table_row(holdout, label)[cost_col], "—")
        text = "\n".join(section(md, "Adoption Verdict (holdout only)"))
        self.assertIn("(metered USD): — vs", text)
        self.assertIn("unknown ✗", text)
        self.assertIn("**Verdict: NOT ADOPTED** Config3 (failed: cost)", text)

    def test_unmatched_row_marks_its_config_cost_unknown(self):
        # Metered file exists but covers only the baseline row.
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            write_jsonl(tmpdir / "results.jsonl", SLOW_CAND)
            write_jsonl(tmpdir / "results-metered.jsonl",
                        metered_rows(SLOW_CAND, [0.40, None, 0.01, None]))
            results = report.load_results(str(tmpdir / "results.jsonl"))
            index, present = report.load_metered_index(
                str(tmpdir / "results-metered.jsonl"))
            info = report.join_costs(results, index, present)
            self.assertEqual(info["source"], "unmetered")
            self.assertEqual(len(info["unmatched"]), 2)
            md = report.generate_scorecard(results, BLINDING, info)

        self.assertIn('cost_source: "unmetered"', md)
        self.assertIn("h1_case/C/r1", md)
        holdout = section(md, HOLDOUT)
        cost_col = column(holdout, "Cost/Succ ($)")
        cand = table_row(holdout, "Config3")
        self.assertEqual(cand[cost_col], "—")
        self.assertEqual(cand[column(holdout, "Cost src")], "unmetered")
        self.assertIn("**Verdict: NOT ADOPTED**", md)


class BlindingDirection(unittest.TestCase):
    def test_letter_resolves_to_its_config_name_and_unkeyed_is_marked(self):
        rows = CHEAPER_CAND + [
            row("h2_case", "security", "holdout", "B", 1, wall=20.0, tokens=500),
        ]
        with tempfile.TemporaryDirectory() as td:
            md = build(td, rows, CHEAPER_COSTS + [0.05])
        holdout = section(md, HOLDOUT)
        # Key says Config-name -> letter, rows carry the letter: A is Config2.
        self.assertIsNotNone(table_row(holdout, "Config2"))
        self.assertIsNotNone(table_row(holdout, "Config3"))
        self.assertIsNotNone(table_row(holdout, "B (unkeyed)"))
        self.assertIsNone(table_row(holdout, "Config1"))
        self.assertIn("- Config2 → config **A**", md)


if __name__ == "__main__":
    unittest.main()
