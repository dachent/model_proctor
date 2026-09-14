#!/usr/bin/env python3
"""Usage-unknown tests (#83 TOOL-030 M3 slice, A13).

A13: a truncated or malformed usage record used to become an empty,
zero-priced result — `usage.get(k, 0)` turned missing numbers into zeros and
an unobserved call was recorded as $0. These tests pin that malformed /
torn / missing usage is UNKNOWN: excluded from totals, flagged on the row,
and priced as null, never free. Valid records still meter normally.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # harnesses/kimi-code
RUNNER = ROOT / "runner" / "runner.py"
PRICING = ROOT.parents[1] / "evals" / "pricing.yaml"

spec = importlib.util.spec_from_file_location("runner", str(RUNNER))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15\n'
    'print("PASS")\n'
)

HEALTHY_LINE = {"type": "usage.record", "model": "fireworks/glm-5p3-flash",
                "usage": {"inputOther": 100000, "output": 20000,
                          "inputCacheRead": 50000, "inputCacheCreation": 0}}
# 100000*0.15 + 50000*0.03 + 20000*0.50, per 1e6, rounded to 6 = 0.0265
HEALTHY_COST = 0.0265


def wire_file(tmp, name, lines):
    p = Path(tmp) / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def run_runner(*argv):
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, timeout=120)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


class ScanUsageRecords(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usage-scan-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_healthy_records_meter(self):
        p = wire_file(self.tmp, "h.jsonl",
                      [json.dumps(HEALTHY_LINE), json.dumps(HEALTHY_LINE)])
        records, totals, problems = runner.scan_usage_records(p)
        self.assertEqual(records, 2)
        self.assertEqual(problems, {"unparseable_lines": 0,
                                    "malformed_records": 0})
        self.assertEqual(totals["fireworks/glm-5p3-flash"]["inputOther"], 200000)

    def test_empty_usage_block_is_malformed_not_zero_priced(self):
        # A13: `usage: {}` used to sum as all-zero buckets -> $0.
        bad = dict(HEALTHY_LINE, usage={})
        p = wire_file(self.tmp, "bad.jsonl",
                      [json.dumps(HEALTHY_LINE), json.dumps(bad)])
        records, totals, problems = runner.scan_usage_records(p)
        self.assertEqual(records, 1)
        self.assertEqual(problems["malformed_records"], 1)
        self.assertEqual(totals["fireworks/glm-5p3-flash"]["inputOther"], 100000)

    def test_torn_line_is_unparseable_not_skipped_silently(self):
        p = wire_file(self.tmp, "torn.jsonl",
                      [json.dumps(HEALTHY_LINE), '{"type": "usage.record", "us'])
        records, _totals, problems = runner.scan_usage_records(p)
        self.assertEqual(records, 1)
        self.assertEqual(problems["unparseable_lines"], 1)

    def test_missing_model_and_boolean_fields_are_malformed(self):
        nomodel = {k: v for k, v in HEALTHY_LINE.items() if k != "model"}
        booly = json.loads(json.dumps(HEALTHY_LINE))
        booly["usage"]["inputOther"] = True
        p = wire_file(self.tmp, "m.jsonl",
                      [json.dumps(nomodel), json.dumps(booly)])
        records, totals, problems = runner.scan_usage_records(p)
        self.assertEqual(records, 0)
        self.assertEqual(problems["malformed_records"], 2)
        self.assertEqual(totals, {})

    def test_compat_wrapper_keeps_the_s6_shape(self):
        p = wire_file(self.tmp, "c.jsonl", [json.dumps(HEALTHY_LINE)])
        records, totals = runner.sum_usage_records(p)
        self.assertEqual(records, 1)
        self.assertIn("fireworks/glm-5p3-flash", totals)


class RecordRow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usage-row-")
        self.ws = Path(self.tmp) / "ws"
        self.ws.mkdir()
        (self.ws / "math_utils.py").write_text(FIXED, encoding="utf-8")
        (self.ws / "check.py").write_text(CHECK, encoding="utf-8")
        self.task = Path(self.tmp) / "task.json"
        self.task.write_text(json.dumps({
            "task_id": "t_usage", "prompt": "Fix.",
            "features": {"bounded": True}, "scope": ["math_utils.py"],
            "verifier": {"argv": ["{python}", "check.py"]},
            "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60},
        }), encoding="utf-8")
        rc, out = run_runner("init", "--workspace", str(self.ws),
                             "--task", str(self.task))
        self.assertEqual(rc, 0, out)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _record(self, wire):
        rc, out = run_runner("record", "--workspace", str(self.ws),
                             "--task", str(self.task),
                             "--wire", wire, "--pricing", str(PRICING))
        self.assertEqual(rc, 0, out)
        return out["row"]

    def test_healthy_wire_prices(self):
        row = self._record(wire_file(self.tmp, "h.jsonl",
                                     [json.dumps(HEALTHY_LINE)]))
        self.assertFalse(row["usage_unknown"])
        self.assertAlmostEqual(row["api_cost_usd"], HEALTHY_COST, places=6)

    def test_malformed_wire_is_unknown_never_zero(self):
        bad = wire_file(self.tmp, "bad.jsonl",
                        [json.dumps(HEALTHY_LINE),
                         json.dumps(dict(HEALTHY_LINE, usage={}))])
        row = self._record(bad)
        self.assertTrue(row["usage_unknown"])
        self.assertIsNone(row["api_cost_usd"])       # not 0.0 — A13's point
        self.assertEqual(row["usage_problems"]["malformed_records"], 1)
        # The valid record's partial cost stays visible.
        self.assertAlmostEqual(
            row["cost_usd_by_model"]["fireworks/glm-5p3-flash"],
            HEALTHY_COST, places=6)


if __name__ == "__main__":
    unittest.main()
