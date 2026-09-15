#!/usr/bin/env python3
"""M1-lite acceptance/lifecycle tests (#83, Tier 2 — 2026-09-14).

Four remaining reproduced defects from the 2026-09-07 review, fixed together
because they share one surface — the verify/accept/dispatch lifecycle:

  A01 — verifier timeout: refused receipt (never a crash, never a stale
        green); verify(green) -> verify(timeout) -> accept is blocked.
  A02-lite — accept during a live writer: the journal's open dispatch
        entries refuse acceptance; orphans (older than the ceiling) do not.
  A05 — verifier env sanitization: inherited PYTHONOPTIMIZE can no longer
        strip the verifier's asserts (__debug__ stays true in the child).
  A06 — a green verify clears the failure history (and with it the provider
        circuit breaker): stale failures no longer order a later lateral
        switch on a progressing task.
  #75 — provider-failure circuit breaker: three trailing provider failures
        with one fingerprint refuse further dispatch until an explicit,
        counted --reset-provider-gate (or a green verify clears it).

Zero model calls: dispatch tests use the fake worker / a synthetic journal
entry written directly.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # harnesses/kimi-code
RUNNER = ROOT / "runner" / "runner.py"
FAKE_WORKER = ROOT / "runner" / "tests" / "fake_worker.py"

FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
BUGGY = 'def sum_to_n(n):\n    return sum(range(1, n))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15\n'
    'print("PASS")\n'
)


def run_runner(*argv, env_extra=None, timeout=180):
    env = dict(__import__("os").environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=timeout)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def make_task(tmp, verifier_argv, budget=None, task_id="t1", features=None):
    task = {"task_id": task_id, "prompt": "Fix the bug.",
            "features": features or {"bounded": True},
            "scope": ["math_utils.py"],
            "verifier": {"argv": verifier_argv},
            "budget": budget or {"max_dispatches": 4, "max_stagnant": 3,
                                 "timeout_s": 60}}
    p = Path(tmp) / f"task-{task_id}.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    return str(p)


def make_workspace(tmp, content=FIXED):
    ws = Path(tmp) / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "math_utils.py").write_text(content, encoding="utf-8")
    (ws / "check.py").write_text(CHECK, encoding="utf-8")
    return str(ws)


def state_path_for(ws):
    """The default state root for a workspace (sibling .runner-state)."""
    ws_res = Path(ws).resolve()
    import hashlib as _h
    key = __import__("re").sub(r"[^A-Za-z0-9_.-]+", "_", ws_res.name)
    digest = _h.sha256(str(ws_res).encode("utf-8")).hexdigest()[:8]
    return ws_res.parent / ".runner-state" / f"{key}-{digest}"


class A01VerifierTimeout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m1-a01-")
        self.ws = make_workspace(self.tmp, FIXED)
        self.ok_task = make_task(self.tmp, ["{python}", "check.py"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_timeout_writes_refused_receipt_and_blocks_stale_green(self):
        rc, out = run_runner("init", "--workspace", self.ws,
                             "--task", self.ok_task)
        self.assertEqual(rc, 0, out)
        # Green verify first (creates the receipt this attack would ride).
        rc, out = run_runner("verify", "--workspace", self.ws,
                             "--task", self.ok_task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["passed"])
        # Now a task whose verifier sleeps past a 2s budget -> A01.
        slow_task = make_task(
            self.tmp, ["{python}", "-c", "import time; time.sleep(30)"],
            budget={"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 2},
            task_id="t_slow")
        rc, out = run_runner("init", "--workspace", self.ws,
                             "--task", slow_task, "--reinit")
        self.assertEqual(rc, 0, out)
        rc, out = run_runner("verify", "--workspace", self.ws,
                             "--task", slow_task, timeout=60)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["error"], "verifier_timeout")
        # And acceptance is now blocked: the receipt is red, not stale-green.
        rc, out = run_runner("accept", "--workspace", self.ws,
                             "--task", slow_task)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["reason"], "receipt not green")

    def test_unlaunchable_verifier_is_refused_not_crashed(self):
        bad_task = make_task(self.tmp,
                             ["C:/no/such/interpreter_ever.exe", "check.py"],
                             task_id="t_bad")
        rc, out = run_runner("init", "--workspace", self.ws,
                             "--task", bad_task, "--reinit")
        self.assertEqual(rc, 0, out)
        rc, out = run_runner("verify", "--workspace", self.ws,
                             "--task", bad_task)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["error"], "verifier_launch_failed")


class A05VerifierEnvSanitization(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m1-a05-")
        # The verifier passes ONLY when __debug__ is True — PYTHONOPTIMIZE
        # in the parent env would strip asserts and flip __debug__, making
        # this verifier fail (the A05 attack inverted: optimized envs
        # change verifier behavior).
        self.ws = make_workspace(self.tmp, FIXED)
        self.task = make_task(
            self.tmp, ["{python}", "-c",
                       "import sys; sys.exit(0 if __debug__ else 3)"],
            task_id="t_dbg")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pythonoptimize_in_parent_does_not_reach_verifier(self):
        rc, out = run_runner("init", "--workspace", self.ws,
                             "--task", self.task)
        self.assertEqual(rc, 0, out)
        # PYTHONOPTIMIZE=1 in the RUNNER's own environment: without
        # sanitization the verifier child inherits it, __debug__ flips
        # False, exit 3 -> verify "fails" for an env reason (the review's
        # A05 ran the opposite direction: stripped asserts made failing
        # verifiers pass; both directions are the same env leak).
        rc, out = run_runner("verify", "--workspace", self.ws,
                             "--task", self.task,
                             env_extra={"PYTHONOPTIMIZE": "1"})
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["passed"],
                        "PYTHONOPTIMIZE leaked into the verifier child")


class A06PassClearsFailures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m1-a06-")
        self.ws = make_workspace(self.tmp, BUGGY)
        self.task = make_task(self.tmp, ["{python}", "check.py"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_green_verify_clears_failure_history(self):
        rc, _ = run_runner("init", "--workspace", self.ws, "--task", self.task)
        self.assertEqual(rc, 0)
        rc, out = run_runner("verify", "--workspace", self.ws, "--task", self.task)
        self.assertEqual(rc, 1)
        rc, out = run_runner("status", "--workspace", self.ws)
        self.assertEqual(out["failure_count"], 1)
        # Fix the tree; the next verify goes green and must clear history.
        (Path(self.ws) / "math_utils.py").write_text(FIXED, encoding="utf-8")
        rc, out = run_runner("verify", "--workspace", self.ws, "--task", self.task)
        self.assertEqual(rc, 0)
        rc, out = run_runner("status", "--workspace", self.ws)
        self.assertEqual(out["failure_count"], 0,
                         "A06: failures survived a green verify")


class ProviderCircuitBreaker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m1-cb-")
        self.ws = make_workspace(self.tmp, BUGGY)
        self.task = make_task(self.tmp, ["{python}", "check.py"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _inject_provider_failures(self, n=3, fingerprint="provider:timeout"):
        sp = state_path_for(self.ws) / "state.json"
        state = json.loads(sp.read_text(encoding="utf-8"))
        state["failures"] = [{
            "kind": "provider_or_tool", "fingerprint": fingerprint,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        } for _ in range(n)]
        sp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")

    def test_three_same_fingerprint_provider_failures_open_the_circuit(self):
        rc, _ = run_runner("init", "--workspace", self.ws, "--task", self.task)
        self.assertEqual(rc, 0)
        self._inject_provider_failures(3)
        rc, out = run_runner("dispatch", "--workspace", self.ws,
                             "--task", self.task,
                             "--delegate", str(FAKE_WORKER))
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["error"], "provider_circuit_open")
        self.assertEqual(out["consecutive_failures"], 3)
        self.assertEqual(out["fingerprint"], "provider:timeout")

    def test_reset_provider_gate_is_counted_and_allows_dispatch(self):
        rc, _ = run_runner("init", "--workspace", self.ws, "--task", self.task)
        self.assertEqual(rc, 0)
        self._inject_provider_failures(3)
        rc, out = run_runner("dispatch", "--workspace", self.ws,
                             "--task", self.task,
                             "--delegate", str(FAKE_WORKER),
                             "--reset-provider-gate")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.get("dispatched"))
        rc, out = run_runner("status", "--workspace", self.ws)
        self.assertEqual(out.get("provider_gate_resets"), 1)

    def test_green_verify_clears_the_circuit_implicitly(self):
        rc, _ = run_runner("init", "--workspace", self.ws, "--task", self.task)
        self.assertEqual(rc, 0)
        self._inject_provider_failures(3)
        (Path(self.ws) / "math_utils.py").write_text(FIXED, encoding="utf-8")
        rc, out = run_runner("verify", "--workspace", self.ws, "--task", self.task)
        self.assertEqual(rc, 0, out)
        # Circuit cleared by the green verify (A06): dispatch proceeds.
        rc, out = run_runner("dispatch", "--workspace", self.ws,
                             "--task", self.task,
                             "--delegate", str(FAKE_WORKER),
                             env_extra={"FAKE_WORKER_WRITE": "math_utils.py",
                                        "FAKE_WORKER_CONTENT": FIXED})
        self.assertEqual(rc, 0, out)


class A02AcceptDuringLiveWriter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m1-a02-")
        self.ws = make_workspace(self.tmp, FIXED)
        self.task = make_task(self.tmp, ["{python}", "check.py"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _journal_open_entry(self, sroot, dispatch_id, event="dispatch_open"):
        p = Path(sroot) / "journal.jsonl"
        rec = {"journal_seq": 10_000, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "event": event, "dispatch_id": dispatch_id}
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def test_accept_refuses_while_a_dispatch_is_open(self):
        rc, _ = run_runner("init", "--workspace", self.ws, "--task", self.task)
        self.assertEqual(rc, 0)
        rc, out = run_runner("verify", "--workspace", self.ws, "--task", self.task)
        self.assertEqual(rc, 0, out)
        # Simulate a live writer: a fresh dispatch_open journal entry.
        sroot = state_path_for(self.ws)
        self._journal_open_entry(sroot, "dispatch-live-1")
        rc, out = run_runner("accept", "--workspace", self.ws, "--task", self.task)
        self.assertEqual(rc, 1, out)
        self.assertIn("dispatch_in_flight", out["reason"])
        self.assertEqual(out["live_dispatch_ids"], ["dispatch-live-1"])
        # Close it (finished pair) -> acceptance proceeds. (The receipt is
        # zero-dispatch/nondiscriminating — the workspace was fixed from
        # init — so this is the reviewed --allow-zero-dispatch exception,
        # unrelated to what this test exercises: the in-flight gate.)
        self._journal_open_entry(sroot, "dispatch-live-1",
                                 event="dispatch_finished")
        rc, out = run_runner("accept", "--workspace", self.ws, "--task", self.task,
                             "--allow-zero-dispatch")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["accepted"])


if __name__ == "__main__":
    unittest.main()
