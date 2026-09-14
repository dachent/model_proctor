#!/usr/bin/env python3
"""Zero-model-call defect probes for the ZCode harness (#84 / HARNESS-006).

Reproduces, offline and deterministically, the defects the 2026-09-07 review
found in zproctor.py + hooks/zproctor_gate.mjs at 8ff1c5e:

  A07  verifier substitution — `verify --verifier <python> -c pass` accepts a
       verifier that was never init-pinned; bad source gets accepted.
  A08  dispatch overrun — an unreadable dispatches.log reads as count 0, so
       the gate admits a second Agent request under a cap of 1.
  A09  fail-open evidence — an unreadable gate-failed-open.log reads as
       "no gaps"; acceptance proceeds without the expected evidence.
  C02  journal forgery — the hash chain is unkeyed and recomputable, so a
       same-user writer can rewrite the journal (VERIFY_FAILED ->
       VERIFY_PASSED) and every integrity check still passes.

Each probe includes a positive control where cheap, so a NOT-REPRODUCED
result means the control behaved and the defect path changed — not that the
probe never ran.

Not a unittest module on purpose (the suite's discover pattern is `test*`):
these document PRE-fix behavior. When the #84 fixes land, each probe flips to
NOT-REPRODUCED and should then be converted into a refusing regression test
in this directory. A07 and A09 were fixed and converted 2026-09-09 — see
test_gate_refusals.py; those two probes now report NOT-REPRODUCED, which is
the expected post-fix flip. A08 (dispatch reservation) and C02 (keyed hash
chain) remain open until #83 M1 lands its transactional admission design.

Usage: python harnesses/zcode/tests/probe_defects.py
Exit code is always 0 — this is evidence collection, not a gate.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ZCODE = Path(__file__).resolve().parents[1]
ZPROCTOR = ZCODE / "zproctor.py"
GATE = ZCODE / "hooks" / "zproctor_gate.mjs"

BUGGY = 'def sum_to_n(n):\n    return sum(range(1, n))\n'
FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15, f"sum_to_n(5)={sum_to_n(5)}, expected 15"\n'
    'print("PASS")\n'
)


def run_zp(args, state_root, timeout=120):
    env = dict(os.environ, ZPROCTOR_STATE_ROOT=str(state_root))
    r = subprocess.run([sys.executable, str(ZPROCTOR), *args],
                       capture_output=True, text=True, env=env, timeout=timeout)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def run_gate(payload, state_root, timeout=30):
    env = dict(os.environ, ZPROCTOR_STATE_ROOT=str(state_root), ZPROCTOR_TRACE="0")
    r = subprocess.run(["node", str(GATE)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=timeout)
    denied = "permissionDecision" in (r.stdout or "")
    return r.returncode, denied, (r.stdout or "")


def make_ws(root, buggy=True):
    ws = Path(root) / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "math_utils.py").write_text(BUGGY if buggy else FIXED, encoding="utf-8")
    (ws / "check.py").write_text(CHECK, encoding="utf-8")
    return ws


def roster_glm_agent():
    """Mirror zproctor.load_roster: prefer roster.json, fall back to the example."""
    for name in ("roster.json", "roster.example.json"):
        p = ZCODE / name
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            agent = data.get("lanes", {}).get("substantial")
            if agent and agent != "self":
                return agent
    return None


def lane_init(ws, state_root, task, verifier=True, max_dispatches=None):
    args = ["lane", "--task", task, "--workspace", str(ws), "--multi-module"]
    if max_dispatches is not None:
        args += ["--max-dispatches", str(max_dispatches)]
    rc, out = run_zp(args, state_root)
    assert rc == 0 and out.get("ok"), out
    if verifier:
        rc, out = run_zp(["init", "--task", task, "--workspace", str(ws),
                          "--scope", "math_utils.py",
                          "--verifier", sys.executable, "check.py"], state_root)
        assert rc == 0 and out.get("ok"), out
    return out


def probe_a07(root):
    """verify accepts a substituted verifier; bad source is then accepted."""
    state = Path(root) / "a07-state"
    ws = make_ws(root / "a07", buggy=True)
    lane_init(ws, state, "t_a07")
    rc, out = run_zp(["verify", "--task", "t_a07", "--workspace", str(ws)],
                     state)
    control_failed = (rc != 0 and not out.get("passed"))
    # Substitution: a no-op verifier that was never init-pinned.
    rc, out = run_zp(["verify", "--task", "t_a07", "--workspace", str(ws),
                      "--verifier", sys.executable, "-c", "pass"], state)
    substituted_passed = (rc == 0 and out.get("passed"))
    rc, out = run_zp(["accept", "--task", "t_a07", "--workspace", str(ws)],
                     state)
    accepted_bad_source = (rc == 0 and out.get("ok"))
    reproduced = control_failed and substituted_passed and accepted_bad_source
    return reproduced, (
        f"init-pinned verifier fails on buggy source (control: {control_failed}); "
        f"verify --verifier '{sys.executable} -c pass' passes (substituted_passed: "
        f"{substituted_passed}); accept ok on unchanged buggy source "
        f"(accepted_bad_source: {accepted_bad_source})")


def probe_a08(root):
    """Unreadable dispatches.log reads as 0 used; two gate allows under cap=1."""
    node = shutil.which("node")
    if not node:
        return None, "SKIPPED: node runtime not available"
    agent = roster_glm_agent()
    if not agent:
        return None, "SKIPPED: roster binds no dispatchable substantial agent"
    state = Path(root) / "a08-state"
    ws = make_ws(root / "a08", buggy=True)
    lane_init(ws, state, "t_a08", verifier=True, max_dispatches=1)
    # Control: with a readable log, the second request is denied.
    payload = {"tool_name": "Agent", "tool_input": {"subagent_type": agent},
               "cwd": str(ws.resolve())}
    rc1, denied1, _ = run_gate(payload, state)
    rc2, denied2, _ = run_gate(payload, state)
    control = (not denied1) and denied2
    # Defect path: make dispatches.log unreadable (a directory), reset budget
    # accounting to zero by the gate's own catch-path.
    log = state / (ws.name + "-" + hashlib.sha256(
        str(ws.resolve()).lower().encode()).hexdigest()[:12]) / "t_a08" / "dispatches.log"
    log.unlink()
    log.mkdir()
    rc3, denied3, _ = run_gate(payload, state)
    rc4, denied4, _ = run_gate(payload, state)
    overrun = (not denied3) and (not denied4)
    reproduced = control and overrun
    return reproduced, (
        f"cap=1: readable log denies the 2nd request (control: {control}); "
        f"unreadable log admits request 3 AND 4 (overrun: {overrun})")


def probe_a09(root):
    """Unreadable gate-failed-open.log reads as no-gaps; acceptance proceeds."""
    state = Path(root) / "a09-state"
    ws = make_ws(root / "a09", buggy=True)
    lane_init(ws, state, "t_a09")
    (ws / "math_utils.py").write_text(FIXED, encoding="utf-8")
    rc, out = run_zp(["verify", "--task", "t_a09", "--workspace", str(ws)],
                     state)
    assert rc == 0 and out.get("passed"), out
    # Control: a real fail-open record blocks acceptance.
    fail_log = state / "gate-failed-open.log"
    fail_log.parent.mkdir(parents=True, exist_ok=True)
    fail_log.write_text(f"{int(time.time() * 1000) + 1}\tprobe-control\n",
                        encoding="utf-8")
    rc, out = run_zp(["accept", "--task", "t_a09", "--workspace", str(ws)],
                     state)
    control_blocked = (rc != 0 and out.get("error") == "gate_failed_open")
    # Defect path: the expected-evidence file unreadable -> treated as clean.
    fail_log.unlink()
    fail_log.mkdir()
    rc, out = run_zp(["accept", "--task", "t_a09", "--workspace", str(ws)],
                     state)
    accepted_without_evidence = (rc == 0 and out.get("ok"))
    reproduced = control_blocked and accepted_without_evidence
    return reproduced, (
        f"written fail-open record blocks accept (control: {control_blocked}); "
        f"unreadable log -> accept ok without the expected evidence "
        f"(accepted_without_evidence: {accepted_without_evidence})")


def _event_hash(ev):
    """Byte-identical to zproctor._event_hash — that is the point (C02)."""
    signed = {k: ev.get(k) for k in ("schema", "seq", "type", "ts", "payload",
                                     "prev_hash")}
    return hashlib.sha256(json.dumps(signed, sort_keys=True).encode()).hexdigest()


def probe_c02(root):
    """Forged journal with recomputed unkeyed hashes passes every check."""
    state = Path(root) / "c02-state"
    ws = make_ws(root / "c02", buggy=True)
    lane_init(ws, state, "t_c02")
    rc, out = run_zp(["verify", "--task", "t_c02", "--workspace", str(ws)],
                     state)
    assert rc != 0 and not out.get("passed"), out
    journal = (state / (ws.name + "-" + hashlib.sha256(
        str(ws.resolve()).lower().encode()).hexdigest()[:12])
        / "t_c02" / "events.jsonl")
    events = [json.loads(x) for x in journal.read_text(encoding="utf-8").splitlines()
              if x.strip()]
    # Control: mutate one event WITHOUT recomputing -> chain breaks.
    tampered = [dict(e) for e in events]
    for e in tampered:
        if e["type"] == "VERIFY_FAILED":
            e["type"] = "VERIFY_PASSED"
            break
    journal.write_text("\n".join(json.dumps(e) for e in tampered) + "\n",
                       encoding="utf-8")
    rc, out = run_zp(["status", "--task", "t_c02", "--workspace", str(ws)],
                     state)
    control_detected = not out["projection"]["chain_ok"]
    # Defect path: same mutation, hashes recomputed exactly as zproctor does.
    forged = [dict(e) for e in events]
    for e in forged:
        if e["type"] == "VERIFY_FAILED":
            e["type"] = "VERIFY_PASSED"
            e["payload"]["fingerprint"] = None
    prev = "genesis"
    for e in forged:
        e["prev_hash"] = prev
        e["event_hash"] = _event_hash(e)
        prev = e["event_hash"]
    journal.write_text("\n".join(json.dumps(e) for e in forged) + "\n",
                       encoding="utf-8")
    rc, out = run_zp(["status", "--task", "t_c02", "--workspace", str(ws)],
                     state)
    st = out["projection"]
    chain_clean = st["chain_ok"] and st["receipt"] and st["receipt"]["passed"]
    rc, out = run_zp(["accept", "--task", "t_c02", "--workspace", str(ws)],
                     state)
    accepted_forged = (rc == 0 and out.get("ok"))
    reproduced = control_detected and chain_clean and accepted_forged
    return reproduced, (
        f"naive tamper detected (control: {control_detected}); "
        f"recomputed-hash forge passes chain checks (chain_clean: {chain_clean}); "
        f"accept ok on the forged green journal (accepted_forged: {accepted_forged})")


def main():
    root = Path(tempfile.mkdtemp(prefix="zproctor-probes-"))
    probes = [("A07 verifier substitution", probe_a07),
              ("A08 dispatch overrun", probe_a08),
              ("A09 fail-open evidence", probe_a09),
              ("C02 journal forgery", probe_c02)]
    results = []
    print(f"probing zproctor at {ZPROCTOR} (state roots under {root})\n")
    for name, fn in probes:
        try:
            reproduced, detail = fn(root)
        except Exception as exc:  # probe infrastructure failure, not a verdict
            reproduced, detail = None, f"PROBE ERROR: {exc!r}"
        status = ("REPRODUCED" if reproduced else
                  "NOT-REPRODUCED" if reproduced is False else "SKIPPED")
        print(f"[{status:>13}] {name}\n               {detail}\n")
        results.append({"probe": name, "status": status, "detail": detail})
    shutil.rmtree(root, ignore_errors=True)
    print(json.dumps({"probes": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
