#!/usr/bin/env python3
"""Reference kill predicate — the executable specification for #106 (TOOL-039, #110).

NOT production code. This module encodes, as minimal ordered rules, the
verdicts the five recorded incident classes must produce under the monitoring
redesign. When #106 ships the real predicate (assumed interface AI-2:
harnesses/kimi-code/delegate/monitor.py::decide_kill), production_bridge
replays the same corpus against it; this module stays as the oracle the corpus
was authored against, and its boundary tests are the anti-overfitting control.

Rule order is load-bearing: completion evidence suppresses everything (a
finished job looks exactly like a wedge to write-age/stack rules), and
degraded measurements force abstention before any kill arithmetic. The wall
clock is never read: window["dispatch"] budgets are carried, not consulted.

Python 3.10, standard library only.
"""

STALL_AGE_FLOOR_S = 600        # writes silent this long -> probe joins suspect tail
WEDGE_AGE_CONFIRM_S = 1500     # silence depth required to confirm a wedge
WEDGE_BUSY_CPU_PERCENT = 5.0   # busy CPU separates a wedge from an exited process


def _latest_probe(events):
    for e in reversed(events):
        if e["type"] == "probe":
            return e
    return None


def _sample_consistent(probe, now_s):
    # 2026-09-16/17: negative CPU, negative write age, or a write age older
    # than the dispatch itself are measurement failures, not evidence.
    if probe["cpu_percent"] < 0:
        return False
    age = probe["last_write_age_s"]
    return 0 <= age <= now_s + 60


def reference_decide(window):
    """Assumed #106 interface (AI-2): window -> verdict dict. Pure; no I/O."""
    events = window["events"]
    now_s = window["now_s"]

    probe = _latest_probe(events)
    degraded = bool(
        probe is not None
        and (not probe["probe_ok"] or not _sample_consistent(probe, now_s)))

    for e in events:
        if e["type"] == "completion_evidence":
            # 2026-09-01/09-12: success evidence suppresses every kill path.
            return {"kill": False, "reason": "completion_evidence",
                    "measurement_degraded": degraded,
                    "evidence": {"completion_t": e["t"],
                                 "channel": e["channel"]}}

    if degraded:
        # #109: a monitor that cannot trust its own samples abstains.
        return {"kill": False, "reason": "measurement_degraded",
                "measurement_degraded": True,
                "evidence": {"probe_t": probe["t"],
                             "probe_ok": probe["probe_ok"]}}

    progress_ts = [e["t"] for e in events if e["type"] == "progress"]
    if progress_ts and now_s - progress_ts[-1] < WEDGE_AGE_CONFIRM_S:
        # #105: the progress channel is primary evidence (2026-08-25).
        return {"kill": False, "reason": "progress_fresh",
                "measurement_degraded": False,
                "evidence": {"last_progress_t": progress_ts[-1]}}

    probes = [e for e in events if e["type"] == "probe" and e["probe_ok"]]
    tail = []
    for p in reversed(probes):
        if (p["cpu_percent"] >= WEDGE_BUSY_CPU_PERCENT
                and p["last_write_age_s"] >= STALL_AGE_FLOOR_S):
            tail.append(p)
        else:
            break
    tail.reverse()
    if (len(tail) >= 2
            and tail[-1]["last_write_age_s"] >= WEDGE_AGE_CONFIRM_S
            and tail[-1]["stack_frames"]
            and tail[-1]["stack_frames"] == tail[-2]["stack_frames"]):
        # 2026-09-25 attempts 06/07: busy CPU + silent writes + identical
        # consecutive stacks = confirmed wedge; kill WITH the stack evidence.
        return {"kill": True, "reason": "confirmed_wedge",
                "measurement_degraded": False,
                "evidence": {"stack_frames": tail[-1]["stack_frames"],
                             "silent_writes_s": tail[-1]["last_write_age_s"],
                             "confirming_probes": len(tail)}}

    return {"kill": False, "reason": "insufficient_evidence",
            "measurement_degraded": False, "evidence": {}}
