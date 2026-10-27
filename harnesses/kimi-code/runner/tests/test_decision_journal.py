#!/usr/bin/env python3
"""Control-plane decisions must leave a trace (#119, swarm 2026-10-26).

Two measurement holes, one fix each:

  * Every verify/accept/dispatch refusal was `raise SystemExit(_emit(...))` —
    stdout only. The journals held nine event types and none of them was a
    refusal, so "how often is acceptance refused, and why" had a denominator of
    zero: one `accepted:false` state file in eight was the entire corpus.
    `_refuse` now appends `{event: "decision", action, reason, exit}` before
    emitting, fail-open (a journal write failure must never mask the refusal).
  * `record` metered only inside `if args.wire:`, so a dispatched task asked to
    meter (`--pricing` given) without `--wire` wrote a row the A13 "an
    unobserved call is never $0" guard could never see. That combination now
    refuses. A dispatched task recorded with neither flag is a flat-rate record
    (a subscription worker emits no per-token usage, so $0 is the truth) and
    stays legal.

Acceptance also records the evidence it consumed (receipt path + hash) and
whether any worker evidence existed, so an acceptance stays auditable after
the receipt rotates.

Run: python -m unittest discover -s runner/tests -v
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # harnesses/kimi-code
RUNNER = ROOT / "runner" / "runner.py"
FAKE_WORKER = Path(__file__).resolve().parent / "fake_worker.py"
PRICING = ROOT.parents[1] / "evals" / "pricing.yaml"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hermetic_scratch  # noqa: E402  (#115: keep dispatch scratch in temp)
hermetic_scratch.install()

BUGGY = 'def sum_to_n(n):\n    return sum(range(1, n))\n'
FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15, f"sum_to_n(5)={sum_to_n(5)}, expected 15"\n'
    'print("PASS")\n'
)


def run_runner(*argv, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=120)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def journal_lines(sdir):
    jr = Path(sdir) / "journal.jsonl"
    if not jr.is_file():
        return []
    return [json.loads(ln) for ln in jr.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def decisions(sdir):
    return [e for e in journal_lines(sdir) if e.get("event") == "decision"]


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-decision-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = str(self.make_ws())
        self.task = self.make_task()

    def make_ws(self):
        ws = Path(self.tmp) / "ws"
        ws.mkdir()
        (ws / "math_utils.py").write_text(BUGGY, encoding="utf-8")
        (ws / "check.py").write_text(CHECK, encoding="utf-8")
        return ws

    def make_task(self, **budget):
        task = {
            "task_id": "t1",
            "prompt": "Fix the bug.",
            "features": {"bounded": True, "known_location": True,
                         "objective_acceptance": True},
            "scope": ["math_utils.py"],
            "verifier": {"argv": ["{python}", "check.py"]},
            "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60,
                       **budget},
        }
        p = Path(self.tmp) / "task.json"
        p.write_text(json.dumps(task), encoding="utf-8")
        return str(p)

    def init(self):
        rc, out = run_runner("init", "--workspace", self.ws, "--task", self.task)
        self.assertEqual(rc, 0, out)
        return Path(out["state_dir"])

    def dispatch(self, content=FIXED):
        env = {} if content is None else {"FAKE_WORKER_WRITE": "math_utils.py",
                                          "FAKE_WORKER_CONTENT": content}
        return run_runner("dispatch", "--workspace", self.ws,
                          "--task", self.task, "--delegate", str(FAKE_WORKER),
                          env_extra=env)

    def write_wire(self, lines=()):
        p = Path(self.tmp) / "wire.jsonl"
        p.write_text("".join(json.dumps(e) + "\n" for e in lines),
                     encoding="utf-8")
        return str(p)


class RefusalIsJournaled(_Base):
    """A refusal the leader never sees again must survive in the journal."""

    def test_refused_accept_writes_decision_record(self):
        sdir = self.init()
        self.dispatch()
        rc, ver = run_runner("verify", "--workspace", self.ws,
                             "--task", self.task)
        self.assertEqual(rc, 0, ver)

        # Mutate the tree after the green receipt: #17's stale-receipt refusal.
        (Path(self.ws) / "math_utils.py").write_text(BUGGY, encoding="utf-8")
        rc, out = run_runner("accept", "--workspace", self.ws,
                             "--task", self.task)
        self.assertEqual(rc, 1, out)
        self.assertIn("stale_receipt", out["reason"])
        self.assertNotIn("error", out, "the refusal emit contract is unchanged")

        recs = decisions(sdir)
        self.assertEqual(len(recs), 1, recs)
        self.assertEqual(recs[0]["action"], "accept")
        self.assertTrue(recs[0]["reason"].startswith("stale_receipt"), recs[0])
        self.assertEqual(recs[0]["exit"], 1)
        self.assertIn("journal_seq", recs[0])
        self.assertEqual(journal_lines(sdir)[-1]["event"], "decision")

    def test_failed_verify_is_journaled(self):
        # The worker completes without writing: the tree still fails the check.
        sdir = self.init()
        self.dispatch(content=None)
        rc, out = run_runner("verify", "--workspace", self.ws,
                             "--task", self.task)
        self.assertEqual(rc, 1, out)
        self.assertFalse(out["passed"])
        recs = decisions(sdir)
        self.assertEqual([(r["action"], r["reason"], r["exit"])
                          for r in recs],
                         [("verify", "verifier_failed", 1)], recs)

    def test_dispatch_budget_refusal_is_journaled(self):
        self.task = self.make_task(max_dispatches=1)
        sdir = self.init()
        rc, out = self.dispatch()
        self.assertEqual(rc, 0, out)
        self.assertEqual(decisions(sdir), [], "a legal dispatch is not a decision")

        rc, out = self.dispatch()
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["error"], "dispatch budget exhausted")
        recs = decisions(sdir)
        self.assertEqual([(r["action"], r["reason"], r["exit"])
                          for r in recs],
                         [("dispatch", "dispatch budget exhausted", 1)], recs)


class RecordRequiresWire(_Base):
    """`--pricing` asks to meter; that request needs a usage trail."""

    def test_pricing_without_wire_fails_closed_on_dispatched_task(self):
        sdir = self.init()
        self.dispatch()
        rc, out = run_runner("record", "--workspace", self.ws,
                             "--task", self.task, "--pricing", str(PRICING))
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["error"], "wire_required_for_dispatched_task")
        self.assertIn("you passed --pricing but no --wire", out["detail"])
        self.assertEqual(out["dispatches"], 1)
        self.assertFalse((sdir / "tasks.jsonl").is_file(),
                         "a metered row was written with nothing to meter")

        # A13 survives: a wire with no usage.record is UNKNOWN, never $0.
        rc, out = run_runner("record", "--workspace", self.ws,
                             "--task", self.task, "--wire", self.write_wire(),
                             "--pricing", str(PRICING))
        self.assertEqual(rc, 0, out)
        row = out["row"]
        self.assertTrue(row["usage_unknown"], row)
        self.assertIsNone(row["api_cost_usd"], row)

    def test_flat_rate_record_needs_no_wire(self):
        """Neither flag = a flat-rate record (the Codex bridge: a subscription
        worker emits no per-token usage, so $0 is the truth, not blindness)."""
        sdir = self.init()
        self.dispatch()
        rc, out = run_runner("record", "--workspace", self.ws,
                             "--task", self.task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["recorded"], out)
        self.assertEqual(out["row"]["dispatches"], 1)
        # Nothing was metered, so the row says nothing about cost — it does not
        # claim $0 through a usage_unknown flag either.
        self.assertNotIn("api_cost_usd", out["row"])
        self.assertNotIn("usage_unknown", out["row"])
        self.assertTrue((sdir / "tasks.jsonl").is_file())

    def test_record_without_wire_still_works_when_nothing_dispatched(self):
        sdir = self.init()
        rc, out = run_runner("record", "--workspace", self.ws,
                             "--task", self.task)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["row"]["dispatches"], 0)
        self.assertTrue((sdir / "tasks.jsonl").is_file())


class AcceptanceProvenance(_Base):
    """Acceptance names the evidence it consumed."""

    def test_accept_records_receipt_path_hash_and_dispatch_evidence(self):
        sdir = self.init()
        self.dispatch()
        run_runner("verify", "--workspace", self.ws, "--task", self.task)
        rc, out = run_runner("accept", "--workspace", self.ws,
                             "--task", self.task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["accepted"])

        receipt = sdir / "receipt-t1.json"
        expected = hashlib.sha256(receipt.read_bytes()).hexdigest()
        rc, st = run_runner("status", "--workspace", self.ws)
        self.assertEqual(rc, 0, st)
        self.assertEqual(Path(st["receipt_path"]), receipt)
        self.assertEqual(st["receipt_sha256"], expected)
        self.assertIs(st["accepted_with_dispatch"], True)
        # Recorded on state, not only in the status projection.
        state = json.loads((sdir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["receipt_sha256"], expected)

    def test_zero_dispatch_accept_is_recorded_not_refused(self):
        # A3 (#73) keeps the override legal: provenance is recorded, not gated.
        fixed_ws = Path(self.tmp) / "ws0"
        fixed_ws.mkdir()
        (fixed_ws / "math_utils.py").write_text(FIXED, encoding="utf-8")
        (fixed_ws / "check.py").write_text(CHECK, encoding="utf-8")
        self.ws = str(fixed_ws)
        sdir = self.init()
        run_runner("verify", "--workspace", self.ws, "--task", self.task)
        rc, out = run_runner("accept", "--workspace", self.ws,
                             "--task", self.task, "--allow-zero-dispatch")
        self.assertEqual(rc, 0, out)
        rc, st = run_runner("status", "--workspace", self.ws)
        self.assertIs(st["accepted_with_dispatch"], False)
        self.assertEqual(st["receipt_sha256"],
                         hashlib.sha256(
                             (sdir / "receipt-t1.json").read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
