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



class _GuardBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class ExplicitFlashOverrideTests(_GuardBase):
    """The override init documents must survive dispatch.

    Regression for the deadlock: init accepted lane:"flash" (task.get("lane")
    is truthy, so the carve-out applied) while dispatch refused it
    unconditionally, telling the operator to "re-init with an explicit `lane`
    override" -- which is what they had just done. The pre-existing
    test_init_allows_explicit_lane_override uses lane="glm", so it never
    reached the disagreement.
    """

    def test_explicit_flash_survives_init_and_dispatch(self):
        d = self.tmp / "r1"
        d.mkdir()
        receipt = d / "doctor_ok.log"
        receipt.write_text("ALL GREEN", encoding="utf-8")
        t = _write_task(self.tmp, lane="flash",
                        preflight_receipts=[str(receipt)])
        ws = self.tmp / "wsf"
        ws.mkdir()
        r = _run(["init", "--workspace", str(ws), "--task", str(t)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["lane"], "flash")
        self.assertTrue(out["production_guard"]["production_task"])

        r2 = _dispatch(ws, t)
        out2 = json.loads(r2.stdout)
        self.assertNotIn("error", out2)
        self.assertEqual(out2.get("envelope_status"), "completed")

    def test_feature_declared_flash_still_refused(self):
        """No explicit lane: features must not smuggle production into flash."""
        t = _write_task(self.tmp)          # features -> flash, no `lane` key
        ws = self.tmp / "wsg"
        ws.mkdir()
        r = _run(["init", "--workspace", str(ws), "--task", str(t)])
        self.assertNotEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["error"], "flash_lane_forbidden_production_runner")
        # A lane refusal is a refusal (1), not missing/bad config (3).
        self.assertEqual(r.returncode, 1)


class PreflightFreshnessTests(_GuardBase):
    """Existence is satisfied by `touch`; replay is what age catches."""

    def test_dispatch_refuses_stale_receipt(self):
        import os
        import time as _time
        d = self.tmp / "r2"
        d.mkdir()
        receipt = d / "battery_green.log"
        receipt.write_text("ALL GREEN", encoding="utf-8")
        old = _time.time() - (8 * 24 * 3600)     # last week
        os.utime(receipt, (old, old))
        t = _write_task(self.tmp, lane="glm",
                        preflight_receipts=[str(receipt)])
        ws = self.tmp / "wsh"
        ws.mkdir()
        self.assertEqual(
            _run(["init", "--workspace", str(ws), "--task", str(t)]).returncode, 0)
        r = _dispatch(ws, t)
        self.assertNotEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["error"], "preflight_receipt_stale")
        self.assertTrue(any("battery_green.log" in s for s in out["stale"]))

    def test_budget_can_widen_the_window(self):
        import os
        import time as _time
        d = self.tmp / "r3"
        d.mkdir()
        receipt = d / "doctor.log"
        receipt.write_text("ALL GREEN", encoding="utf-8")
        old = _time.time() - (8 * 24 * 3600)
        os.utime(receipt, (old, old))
        t = _write_task(
            self.tmp, lane="glm", preflight_receipts=[str(receipt)],
            budget={"max_dispatches": 1, "max_stagnant": 2, "timeout_s": 60,
                    "max_preflight_age_s": 30 * 24 * 3600})
        ws = self.tmp / "wsi"
        ws.mkdir()
        self.assertEqual(
            _run(["init", "--workspace", str(ws), "--task", str(t)]).returncode, 0)
        out = json.loads(_dispatch(ws, t).stdout)
        self.assertNotIn("error", out)

    def test_fresh_receipt_age_is_recorded(self):
        d = self.tmp / "r4"
        d.mkdir()
        receipt = d / "doctor.log"
        receipt.write_text("ALL GREEN", encoding="utf-8")
        t = _write_task(self.tmp, lane="glm",
                        preflight_receipts=[str(receipt)])
        ws = self.tmp / "wsj"
        ws.mkdir()
        _run(["init", "--workspace", str(ws), "--task", str(t)])
        _dispatch(ws, t)
        r = _run(["status", "--workspace", str(ws)])
        state = json.loads(r.stdout)
        ages = state["dispatches"][-1]["preflight_ages_seconds"]
        self.assertIsNotNone(ages, "preflight evidence left no trace")
        self.assertTrue(any("doctor.log" in k for k in ages))


if __name__ == "__main__":
    unittest.main()
