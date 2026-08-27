#!/usr/bin/env python3
"""Production-runner guard + preflight-receipt mandate (runner/runner.py).

Incident class: fsn_rpt_wk_finops week-run 2026-08-25 — a production pipeline
task was feature-declared bounded and dispatched to the flash lane; worker #1
burned its budget discovering statically-readable prerequisites, and worker #2's
exit killed the freshly launched pipeline (no breakaway).

Run: python -m unittest discover -s runner/tests -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "runner" / "runner.py"
FAKE_WORKER = ROOT / "runner" / "tests" / "fake_worker.py"

PROD_PROMPT = (
    "TASK: Execute the weekly production run per docs/RUNBOOK.md via "
    "pwsh scripts/run_week.ps1 -WeekTag 20260825. Do not improvise."
)
PLAIN_PROMPT = "Fix the typo in README.md section 3."


def _write_task(tmp: Path, **over) -> Path:
    task = {
        "task_id": "t-guard",
        "prompt": PROD_PROMPT,
        "features": {"bounded": True, "known_location": True,
                     "objective_acceptance": True},
        "scope": ["src/"],
        "verifier": {"argv": ["{python}", "-c", "print('ok')"]},
        "budget": {"max_dispatches": 1, "max_stagnant": 2, "timeout_s": 60},
    }
    task.update(over)
    p = tmp / "task.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    return p


def _run(args):
    return subprocess.run([sys.executable, str(RUNNER)] + args,
                          capture_output=True, text=True, timeout=120)


def _dispatch(ws, task):
    """Dispatch hermetically through the fixture fake delegate."""
    return _run(["dispatch", "--workspace", str(ws), "--task", str(task),
                 "--delegate", str(FAKE_WORKER)])


class ProductionGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_lane_advisory_flags_production_flash(self):
        t = _write_task(self.tmp)
        r = _run(["lane", "--task", str(t)])
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["lane"], "flash")           # frozen table unchanged
        self.assertTrue(out["production_task"])
        self.assertIn("flash_lane_forbidden_production_runner", out["guard"])

    def test_lane_plain_task_unaffected(self):
        t = _write_task(self.tmp, prompt=PLAIN_PROMPT)
        r = _run(["lane", "--task", str(t)])
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertNotIn("production_task", out)
        self.assertNotIn("guard", out)

    def test_init_refuses_flash_without_explicit_override(self):
        t = _write_task(self.tmp)
        ws = self.tmp / "ws"
        ws.mkdir()
        r = _run(["init", "--workspace", str(ws), "--task", str(t)])
        self.assertNotEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["error"], "flash_lane_forbidden_production_runner")

    def test_init_allows_explicit_lane_override(self):
        t = _write_task(self.tmp, lane="glm",
                        prompt=PROD_PROMPT)  # explicit override wins
        ws = self.tmp / "ws"
        ws.mkdir()
        r = _run(["init", "--workspace", str(ws), "--task", str(t)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["lane"], "glm")
        self.assertTrue(out["production_guard"]["production_task"])

    def test_dispatch_requires_preflight_receipts(self):
        t = _write_task(self.tmp, lane="glm")
        ws = self.tmp / "ws"
        ws.mkdir()
        r = _run(["init", "--workspace", str(ws), "--task", str(t)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # No preflight_receipts in the task at all -> refuse.
        r2 = _dispatch(ws, t)
        self.assertNotEqual(r2.returncode, 0)
        out = json.loads(r2.stdout)
        self.assertEqual(out["error"], "preflight_receipt_required")

    def test_dispatch_refuses_missing_receipt_files(self):
        d = self.tmp / "receipts"
        d.mkdir()
        t = _write_task(
            self.tmp, lane="glm",
            preflight_receipts=[str(d / "battery_green.log")])
        ws = self.tmp / "ws2"
        ws.mkdir()
        self.assertEqual(_run(["init", "--workspace", str(ws), "--task", str(t)]).returncode, 0)
        r = _dispatch(ws, t)
        out = json.loads(r.stdout)
        self.assertEqual(out["error"], "preflight_receipt_required")
        self.assertTrue(any("battery_green.log" in m for m in out["missing"]))

    def test_dispatch_accepts_existing_receipts_up_to_delegate_boundary(self):
        d = self.tmp / "receipts2"
        d.mkdir()
        receipt = d / "doctor_ok.log"
        receipt.write_text("ALL GREEN", encoding="utf-8")
        t = _write_task(self.tmp, lane="glm",
                        preflight_receipts=[str(receipt)])
        ws = self.tmp / "ws3"
        ws.mkdir()
        self.assertEqual(_run(["init", "--workspace", str(ws), "--task", str(t)]).returncode, 0)
        r = _dispatch(ws, t)
        out = json.loads(r.stdout)
        # Guard passed; the run proceeds into the (fixture) delegate and the
        # fake worker completes — no guard error may appear.
        self.assertNotIn("error", out)
        self.assertEqual(out.get("envelope_status"), "completed")


if __name__ == "__main__":
    unittest.main()
