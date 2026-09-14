#!/usr/bin/env python3
"""q_report tests (#96) — the ledger-is-the-instrument claim, executable.

Synthetic rows in both shapes (ledger tasks.jsonl, pilot summaries), plus
the committed eval evidence as a live input: the tool must recover the
measured q from rows the experiments actually produced.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

EVALS = Path(__file__).resolve().parents[1]
Q_REPORT = EVALS / "q_report.py"


def write_rows(tmp, name, rows):
    p = Path(tmp) / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                 encoding="utf-8")
    return str(p)


def run_q(*sources):
    r = subprocess.run([sys.executable, str(Q_REPORT), *sources, "--json"],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, json.loads(r.stdout) if r.stdout.strip() else {}


LEDGER_FIRST = {"task_id": "t1", "lane": "flash", "dispatches": 1,
                "accepted": True, "failures": 0}
LEDGER_FIRST2 = {"task_id": "t2", "lane": "flash", "dispatches": 1,
                 "accepted": True, "failures": 0}
LEDGER_REPAIR = {"task_id": "t3", "lane": "flash", "dispatches": 2,
                 "accepted": True, "failures": 1}
LEDGER_FAIL = {"task_id": "t4", "lane": "flash", "dispatches": 3,
               "accepted": False, "failures": 3}
PILOT_FIRST = {"task_id": "q1", "arm": "flash", "accepted": True,
               "attempts": [{"agent": "glm-flash-worker"}]}
PILOT_SWITCHED = {"task_id": "q2", "arm": "flash", "accepted": True,
                  "attempts": [{}, {}], "switched_to": "glm"}
PILOT_ERROR = {"task_id": "q3", "arm": "flash", "accepted": False,
               "attempts": [], "error": "init failed"}


class QReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="q-report-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ledger_shape_q(self):
        p = write_rows(self.tmp, "ledger.jsonl",
                       [LEDGER_FIRST, LEDGER_FIRST2, LEDGER_REPAIR, LEDGER_FAIL])
        rc, data = run_q(p)
        self.assertEqual(rc, 0)
        self.assertEqual(data["aggregate"]["units"], 4)
        self.assertEqual(data["aggregate"]["first_pass"], 2)
        self.assertEqual(data["aggregate"]["q"], 0.5)
        src = data["per_source"][0]
        self.assertEqual(src["escalated"], 2)  # LEDGER_REPAIR + LEDGER_FAIL
        self.assertEqual(src["shape"], ["ledger"])

    def test_pilot_shape_q(self):
        p = write_rows(self.tmp, "pilot.jsonl",
                       [PILOT_FIRST, PILOT_FIRST, PILOT_SWITCHED, PILOT_ERROR])
        rc, data = run_q(p)
        self.assertEqual(rc, 0)
        self.assertEqual(data["aggregate"]["units"], 4)
        self.assertEqual(data["aggregate"]["first_pass"], 2)
        self.assertEqual(data["aggregate"]["q"], 0.5)

    def test_first_pass_requires_green_and_no_failures(self):
        # dispatches == 1 but failures > 0 and not accepted -> not first-pass.
        row = {"task_id": "t", "lane": "flash", "dispatches": 1,
               "accepted": False, "failures": 1}
        p = write_rows(self.tmp, "one.jsonl", [row])
        rc, data = run_q(p)
        self.assertEqual(data["aggregate"]["first_pass"], 0)
        self.assertEqual(data["aggregate"]["q"], 0.0)

    def test_unknown_rows_are_skipped_not_counted(self):
        p = write_rows(self.tmp, "junk.jsonl", [{"nonsense": True}, LEDGER_FIRST])
        rc, data = run_q(p)
        self.assertEqual(data["aggregate"]["units"], 1)
        self.assertEqual(data["aggregate"]["q"], 1.0)

    def test_committed_evidence_is_readable(self):
        # Live input: the E-ship plain arm committed on main. Shape must be
        # pilot-style; q must be a number in [0, 1].
        eship = EVALS / "eship-2026-09-10.jsonl"
        if not eship.is_file():
            self.skipTest("eship evidence not present")
        rc, data = run_q(str(eship))
        self.assertEqual(rc, 0)
        self.assertIsNotNone(data["aggregate"]["q"])
        self.assertGreaterEqual(data["aggregate"]["q"], 0.0)
        self.assertLessEqual(data["aggregate"]["q"], 1.0)


if __name__ == "__main__":
    unittest.main()
