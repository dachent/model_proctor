#!/usr/bin/env python3
"""Generate the frozen smoke fixtures for the escalation evaluator.

Durability doctrine (owner directive 2026-08-25): everything on ``C:\\`` is
ephemeral, so the regression cannot depend on the live kimi session wires. Each
fixture below is a *reduced* wire, synthesized from the incidents named in
``review/escalation-audit-20261029.md`` for one audited session, keeping the
real event vocabulary (top-level types and loop-event shapes are copied from
the live wires, not invented).

The generator is documentation, not a runtime dependency: the tests read only
the committed ``*.jsonl`` files, so a future change to this script cannot
silently move the assertions. Re-run it deliberately:

    python harnesses/kimi-code/wirewatch/tests/fixtures/gen_fixtures.py

Deterministic: no wall clock, no environment, sorted JSON keys.
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wire"

# The audited sessions' own clock, so the fixtures' timestamps sit where the
# incidents did (2026-10-29 ~00:28 local) without pretending to be full copies.
S1_BASE = 1793234880000
S2_BASE = 1793032946000
S3_BASE = 1793118598000


def _loop(inner, time, turn=None):
    event = dict(inner)
    if turn is not None:
        event["turnId"] = str(turn)
    return {"type": "context.append_loop_event", "agentId": "main",
            "event": event, "time": time}


def call(time, name, args, call_id, turn=None):
    return _loop({"type": "tool.call", "name": name, "args": args,
                  "toolCallId": call_id}, time, turn)


def result(time, call_id, output, is_error=False, turn=None):
    payload = {"output": output, "durationMs": 7}
    if is_error:
        payload["isError"] = True
    return _loop({"type": "tool.result", "toolCallId": call_id,
                  "result": payload}, time, turn)


def text(time, body, turn=None):
    return _loop({"type": "content.part",
                  "part": {"type": "text", "text": body}}, time, turn)


def turn_started(time, turn):
    return {"type": "agent.turn.started", "turnId": turn, "time": time}


def turn_ended(time, turn, duration_ms, reason="completed"):
    return {"type": "turn.ended", "turnId": turn, "durationMs": duration_ms,
            "reason": reason, "time": time}


def prompt(time, body):
    return {"type": "turn.prompt", "agentId": "main",
            "input": [{"type": "text", "text": body}], "time": time}


def steer(time, body):
    return {"type": "turn.steer", "agentId": "main",
            "origin": {"kind": "user"},
            "input": [{"type": "text", "text": body}], "time": time}


def llm(time, alias="fireworks/deepseek-v4p1-flash-1017-spot"):
    return {"type": "llm.request", "agentId": "main", "kind": "loop",
            "provider": "openai", "model": "accounts/fireworks/routers/x",
            "modelAlias": alias, "time": time}


def write(name, events):
    OUT.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e, sort_keys=True) for e in events]
    (OUT / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(events)


# ── 65f71599 — the proctor used, the proctor bypassed ───────────────────
def session_65f71599():
    """Named incidents reproduced:
    S1-h  three byte-identical bb.ps1 "HTTP 404" failures at 01:49  -> T5
    S1-f  the test file edited 7x inside 3 minutes at 01:19-01:21     -> T6
    S1-g  verify passed:true AND verifier_nondiscriminating:true with
          a dispatch, accept proceeded at 01:45                      -> T2 stop
    S3-j  47-minute turn that ends on a re-dispatch, no repo change
          after it                                                   -> T9
    """
    base = S1_BASE
    ev = [{"type": "metadata", "protocol_version": "1.4", "time": base},
          prompt(base - 1000, "Run this week's data (eow 10/24/26). "
                              "Use main 840ee7c."),
          turn_started(base, 0)]
    # A pinned-tree drift (T4, not implemented) sits first, as it did live.
    ev += [call(base + 1000, "Bash",
                {"command": "git rev-parse HEAD"}, "c-rev", 0),
           result(base + 1100, "c-rev",
                  "746cacbd384cc05df61a523f6603fde930ae2a4a\n"),
           llm(base + 1200), turn_ended(base + 600000, 0, 600000)]

    t1 = base + 700000
    ev += [turn_started(t1, 1)]
    # S1-h: three identical failures. The live trio differed in command
    # preambles; the incident's identity is the failure, so the fixture keeps
    # the exact (command, error-tail) pair the trigger defines.
    stop_cmd = ('pwsh -NoProfile -NonInteractive -File C:/Dev/bin/bb.ps1 api '
                '-Method POST -Path "/repositories/fsnetwork1/fsn_rpt_wk_finops'
                '/pipelines/1431/stop"')
    stop_tail = ("Exception: C:\\Dev\\bin\\bb.ps1:114\r\n"
                 "  bb: HTTP 404 on POST\r\n"
                 "Command failed with exit code: 1.")
    for i in range(3):
        cid = f"c-bb-{i}"
        ev += [call(t1 + 1000 + i * 20000, "Bash", {"command": stop_cmd},
                    cid, 1),
               result(t1 + 1050 + i * 20000, cid, stop_tail, True, 1)]
    # S1-f: seven edits to the same test file in three minutes.
    test_path = "ta_extract/tests/test_run_all_ar_step.py"
    for i in range(7):
        cid = f"c-edit-{i}"
        ev += [call(t1 + 100000 + i * 25000, "Edit",
                    {"path": test_path, "old_string": "x", "new_string": "y"},
                    cid, 1),
               result(t1 + 100020 + i * 25000, cid, "ok\n", False, 1)]
    # A dispatch at the end of the turn: motion, and nothing changed after it.
    ev += [call(t1 + 2700000, "Bash",
                {"command": "cd C:/Tools/model-proctor && python delegate.py "
                            "--model fireworks/ember-1 --workspace C:/Dev/ws "
                            "--task 're-check Phase 0'"}, "c-disp", 1),
           result(t1 + 2700200, "c-disp", '{"status": "started"}\n', False, 1),
           llm(t1 + 2800000), turn_ended(t1 + 2820000, 1, 2820000)]

    t2 = t1 + 3000000
    receipt = json.dumps({
        "accepted": True, "agents_used": ["glm-flash-worker"], "dispatches": 1,
        "failures": 0, "lane": "flash", "recorded_at": "2026-10-29T01:45:12",
        "tamper_detected": False, "task_id": "fsn-week-run-20261020",
        "verifier_nondiscriminating": True, "wall_time_seconds": 94.282},
        sort_keys=True)
    ev += [turn_started(t2, 2),
           call(t2 + 1000, "Bash", {"command": "python C:/Tools/model-proctor/"
                                               "runner.py record --workspace "
                                               "C:/Dev/ws --task t.json"},
                "c-rec", 2),
           result(t2 + 2000, "c-rec", receipt, False, 2),
           text(t2 + 3000, "Phase 0 is passing now.", 2),
           result(t2 + 4000, "c-phase", "REFUSED [blocked_root_seed]\n", True,
                  2),
           turn_ended(t2 + 600000, 2, 600000)]
    return ev


# ── 6a97dd66 — the gate was never initialized ───────────────────────────
def session_6a97dd66():
    """Named incidents reproduced:
    S2-j  five owner "Why did you stop?" prompts (T14) — fired on the pair
          at 10-28 23:19
    S2-d  generations: 4 branches on WKFINOPS-684, one of them rebased 3x
          (T8) at 10-29 06:20
    S2-k  the 4-hour turn 31 and the 60-minute turn 198 (T9)
    S2-l  82 h wall against 4x 1 h authorized dispatch budgets (T10, needs
          the fixture journal; without it the evaluator says unmeasurable)
    """
    base = S2_BASE
    ev = [{"type": "metadata", "protocol_version": "1.4", "time": base},
          prompt(base - 500, "Use /model-proctor and /using-superpowers and "
                             "TDD. Work in an isolated feature branch."),
          llm(base), turn_started(base, 0)]

    # S2-a: the skill was loaded and the only runner call was a status probe.
    # T1 is not implemented (the wire has no skill-load event, disproof R2) —
    # the fixture keeps the evidence so the gap is visible rather than hidden.
    ev += [call(base + 5000, "Bash",
                {"command": "python C:/Tools/model-proctor/runner.py status "
                            "--workspace C:/Dev/ws"}, "c-status", 0),
           result(base + 5100, "c-status",
                  '{"error": "runner not initialized"}\n', True, 0),
           turn_ended(base + 600000, 0, 600000)]

    t1 = base + 700000
    ev += [turn_started(t1, 1),
           prompt(t1 - 1000, "Why did you stop? Do not wait on merges"),
           steer(t1 + 2000, "Why did you stop? Do not wait on merges."),
           llm(t1 + 3000),
           call(t1 + 4000, "Bash", {"command": "gh pr list --state open"},
                "c-pr", 1),
           result(t1 + 4100, "c-pr", "568\topen\n569\topen\n", False, 1),
           turn_ended(t1 + 600000, 1, 600000)]

    # S2-d: branch generations for one ticket, then a rebase loop on one.
    t2 = base + 1400000
    ev += [turn_started(t2, 2), llm(t2 + 1000)]
    for i, name in enumerate([
            "feat/wkfinops-684-on-int",
            "feat/wkfinops-684-on-int-2",
            "feat/wkfinops-684-boolean-pin-test-on-int",
            "feat/wkfinops-684-metric-surface-on-int"]):
        cid = f"c-br-{i}"
        ev += [call(t2 + 10000 + i * 30000, "Bash",
                    {"command": f"git checkout -b {name}"}, cid, 2),
               result(t2 + 10050 + i * 30000, cid,
                      f"Switched to a new branch '{name}'\n", False, 2)]
    for i in range(3):
        cid = f"c-rb-{i}"
        ev += [call(t2 + 200000 + i * 60000, "Bash",
                    {"command": "git rebase origin/integration/2026-10-27 "
                                "feat/wkfinops-684-on-int-2"}, cid, 2),
               result(t2 + 200050 + i * 60000, cid,
                      "Successfully rebased.\n", False, 2)]
    ev += [turn_ended(t2 + 900000, 2, 900000)]

    # S2-k: a 4-hour turn that ends on a tower dispatch and no commit after it.
    t3 = base + 2400000
    ev += [turn_started(t3, 31)]
    for i in range(15):
        cid = f"c-poll-{i}"
        ev += [call(t3 + 60000 * i, "Bash",
                    {"command": f"bb.ps1 pipelines -Recent 6 | head -{i + 1}"},
                    cid, 31),
               result(t3 + 60050 * i, cid, f"pipeline {1400 + i} IN_PROGRESS\n",
                      False, 31)]
    ev += [call(t3 + 900000, "TowerSpawn",
                {"description": "re-anchor 684 on integration"}, "c-tower", 31),
           result(t3 + 900500, "c-tower", '{"spawned": "agent-684-m9"}\n',
                  False, 31),
           llm(t3 + 1000000),
           turn_ended(t3 + 14678616, 31, 14678616)]
    return ev


# ── bbad08a0 — motion instead of completion ─────────────────────────────
def session_bbad08a0():
    """Named incidents reproduced:
    recurring verify-failure: the pyenv `python -c` shim fails identically 4x
        over three days, worked around each time              -> T5 lane_switch
    t8  eight Read->Edit pairs to pod_monitor.py in 42 s       -> T6
    t0->t109  the monitor rewritten 26x across 13 turns        -> T6
    t144  118 minutes of literal sleep in a 140-minute turn    -> T9
    t137  62 minutes of sleep in a 90-call turn                -> T9
    L18600 "Still parked on your one ruling" x3 (T11, not implemented)
    S3-k  one model alias for 1,762 requests (T10 lane-stasis dropped by
          disproof R5; only budget-vs-estimate remains)
    """
    base = S3_BASE
    ev = [{"type": "metadata", "protocol_version": "1.4", "time": base},
          prompt(base - 500, "Monitor the six Tech Debt Cycle pods and flag "
                             "any that are stuck."),
          turn_started(base, 0), llm(base)]

    # The recurring shim failure: same command, same tail, four times.
    shim_cmd = ('pwsh -NoProfile -NonInteractive -Command "& C:/Dev/bin/'
                'pyenv.ps1 python -c \'import json;print(1)\'"')
    shim_tail = ('  File "<string>", line 1\r\n    ||  goto :error\r\n'
                 '    ^\r\nSyntaxError: invalid syntax\r\n'
                 'Command failed with exit code: 1.')
    for i in range(4):
        cid = f"c-shim-{i}"
        ev += [call(base + 10000 + i * 900000, "Bash", {"command": shim_cmd},
                    cid, 0),
               result(base + 10050 + i * 900000, cid, shim_tail, True, 0)]
    ev += [turn_ended(base + 600000, 0, 600000)]

    # t8: 8 Read->Edit pairs to one path in 42 seconds, while the shim
    # failure above is still the session's frozen fingerprint.
    t1 = base + 3800000
    ev += [turn_started(t1, 1)]
    for i in range(8):
        ev += [call(t1 + i * 5000, "Read",
                    {"path": "pods/pod_monitor.py"}, f"c-read-{i}", 1),
               result(t1 + i * 5000 + 100, f"c-read-{i}", "source...\n", False,
                      1),
               call(t1 + i * 5000 + 1000, "Edit",
                    {"path": "pods/pod_monitor.py", "old_string": "a",
                     "new_string": "b"}, f"c-medit-{i}", 1),
               result(t1 + i * 5000 + 1100, f"c-medit-{i}", "ok\n", False, 1)]
    ev += [turn_ended(t1 + 60000, 1, 60000)]

    # t137: 62 minutes of sleep across 90 calls, ending on a delegate relaunch.
    t2 = base + 5000000
    ev += [turn_started(t2, 137)]
    for i in range(88):
        cid = f"c-p137-{i}"
        ev += [call(t2 + 40000 * i, "Bash",
                    {"command": "sleep 42; wc -c watch.log"}, cid, 137),
               result(t2 + 40100 * i, cid, f"{i} watch.log\n", False, 137)]
    ev += [call(t2 + 3600000, "Bash",
                {"command": "cd C:/Tools/model-proctor && python delegate.py "
                            "--model fireworks/ember-1 --workspace C:/Dev/ws "
                            "--task 'review 7 rounds'"}, "c-d137", 137),
           result(t2 + 3600100, "c-d137", '{"status": "started"}\n', False, 137),
           llm(t2 + 3700000), turn_ended(t2 + 4390110, 137, 4390110)]

    # The owner's repeated complaint, twice, word for word.
    t3 = base + 9500000
    ev += [turn_started(t3, 138),
           prompt(t3 - 1000, "Why did you stop?"),
           llm(t3 + 1000),
           turn_ended(t3 + 600000, 138, 600000)]
    t4 = base + 10200000
    ev += [turn_started(t4, 139),
           prompt(t4 - 1000, "Why did you stop?"),
           llm(t4 + 1000),
           turn_ended(t4 + 600000, 139, 600000)]

    # t144: 118 minutes of sleep in a 140-minute turn, no repo change after
    # the delegate relaunches — the owner cancelled it by hand.
    t5 = base + 10900000
    ev += [turn_started(t5, 144)]
    for i in range(100):
        cid = f"c-p144-{i}"
        ev += [call(t5 + 60000 * i, "Bash",
                    {"command": "sleep 70; head -c 300 v_kimi.log"}, cid, 144),
               result(t5 + 60050 * i, cid, "0 bytes\n", False, 144)]
    ev += [call(t5 + 8300000, "Bash",
                {"command": "cd C:/Tools/model-proctor && python delegate.py "
                            "--model fireworks/kimi-k3 --workspace C:/Dev/ws "
                            "--task 'give your judgement'"}, "c-d144", 144),
           result(t5 + 8300100, "c-d144", '{"status": "started"}\n', False, 144),
           llm(t5 + 8380000),
           turn_ended(t5 + 8448936, 144, 8448936, reason="cancelled")]
    return ev


# ── benign look-alikes (must NOT fire) ──────────────────────────────────
def benign_refactor():
    """A 5-edit refactor with distinct outcomes: edits cluster, failures never
    repeat. T6 must stay quiet — no frozen failure fingerprint (disproof R6)."""
    base = S1_BASE
    ev = [{"type": "metadata", "protocol_version": "1.4", "time": base},
          prompt(base - 500, "Rename the helper across the module."),
          turn_started(base, 0), llm(base)]
    for i in range(5):
        cid = f"c-e{i}"
        ev += [call(base + i * 30000, "Edit",
                    {"path": "src/pipeline.py", "old_string": "a",
                     "new_string": "b"}, cid, 0),
               result(base + i * 30000 + 100, cid, "ok\n", False, 0)]
    for i in range(3):
        cid = f"c-t{i}"
        ev += [call(base + 200000 + i * 60000, "Bash",
                    {"command": "python -m unittest discover -s tests"}, cid, 0),
               result(base + 200050 + i * 60000, cid,
                      f"FAILED test_{['alpha', 'beta', 'gamma'][i]} "
                      f"(AssertionError)\n", True, 0)]
    ev += [turn_ended(base + 600000, 0, 600000)]
    return ev


def benign_poll():
    """A long poll with changing output: motion that *is* progress, and no
    dispatch outstanding. T9 must stay quiet."""
    base = S3_BASE
    ev = [{"type": "metadata", "protocol_version": "1.4", "time": base},
          prompt(base - 500, "Watch the CI run and report when it finishes."),
          turn_started(base, 0), llm(base)]
    for i in range(40):
        cid = f"c-p{i}"
        ev += [call(base + i * 60000, "Bash",
                    {"command": "bb.ps1 pipelines -Recent 1"}, cid, 0),
               result(base + i * 60000 + 100, cid,
                      f"pipeline {1400 + i} {25 + i}% COMPLETED\n", False, 0)]
    ev += [turn_ended(base + 2400000, 0, 2400000)]
    return ev


def benign_tdd():
    """TDD: the test file is edited while the verifier is red, then green.
    Redness alone must never fire T6 (disproof R6); the failures are distinct,
    so T5 stays quiet too."""
    base = S1_BASE
    ev = [{"type": "metadata", "protocol_version": "1.4", "time": base},
          prompt(base - 500, "Use TDD for the parser fix."),
          turn_started(base, 0), llm(base)]
    failures = ["FAILED test_parse_alpha\n", "FAILED test_parse_beta\n",
                "FAILED test_parse_gamma\n"]
    for i, failure in enumerate(failures):
        rid = f"c-red-{i}"
        ev += [call(base + i * 60000, "Bash",
                    {"command": "python -m unittest discover -s tests"}, rid, 0),
               result(base + i * 60000 + 50, rid, failure, True, 0)]
        for j in range(3):
            eid = f"c-tedit-{i}-{j}"
            ev += [call(base + i * 60000 + 1000 + j * 2000, "Edit",
                        {"path": "tests/test_parse.py", "old_string": "a",
                         "new_string": "b"}, eid, 0),
                   result(base + i * 60000 + 1100 + j * 2000, eid, "ok\n",
                          False, 0)]
    ev += [call(base + 200000, "Bash",
                {"command": "python -m unittest discover -s tests"}, "c-green", 0),
           result(base + 200100, "c-green", "OK\n", False, 0),
           turn_ended(base + 600000, 0, 600000)]
    return ev


FIXTURES = {
    "65f71599.jsonl": session_65f71599,
    "6a97dd66.jsonl": session_6a97dd66,
    "bbad08a0.jsonl": session_bbad08a0,
    "benign_refactor.jsonl": benign_refactor,
    "benign_poll.jsonl": benign_poll,
    "benign_tdd.jsonl": benign_tdd,
}


def journal_6a97dd66():
    """The runner journal half of T10: four dispatches, each authorizing a
    30-minute wall budget. The session's 4.7-hour reduced span is 2.4x that,
    which is the audit's ">2x estimate" case. Without this file the evaluator
    reports T10 unmeasurable — never a pass."""
    rows = []
    for i in range(4):
        rows.append({
            "journal_seq": i, "at": "2026-10-28T23:00:00",
            "event": "dispatch_open", "task_id": "fsn-ticket-684",
            "dispatch_id": f"d-{i}", "dispatch_seq": i,
            "agent": "glm-flash-worker", "timeout_s": 1800, "runner_pid": 1,
        })
    return rows


JOURNALS = {"journal-6a97dd66.jsonl": journal_6a97dd66}


def main():
    for name in sorted(FIXTURES):
        count = write(name, FIXTURES[name]())
        print(f"{name}: {count} records")
    for name in sorted(JOURNALS):
        count = write(name, JOURNALS[name]())
        print(f"{name}: {count} records")


if __name__ == "__main__":
    main()
