#!/usr/bin/env python3
"""Reference predicate boundary tests (TOOL-039, #110).

Synthetic windows only — none of these appear in the corpus. The predicate
must encode RULES, not answers.
"""
import sys
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

from reference_predicate import (  # noqa: E402
    WEDGE_AGE_CONFIRM_S, reference_decide)

COM = ["combase!CoWaitForMultipleHandles", "worker!com_call"]
IO = ["ntdll!NtReadFile", "worker!read"]
OPEN = {"t": 0, "type": "dispatch_open"}


def probe(t, cpu, age, stacks=None, ok=True):
    if not ok:
        return {"t": t, "type": "probe", "probe_ok": False}
    return {"t": t, "type": "probe", "probe_ok": True, "cpu_percent": cpu,
            "last_write_age_s": age, "stack_frames": stacks}


def prog(t, n=1024):
    return {"t": t, "type": "progress", "bytes_written": n}


def done(t):
    return {"t": t, "type": "completion_evidence",
            "channel": "envelope_completed"}


def window(events, dispatch=None):
    return {"now_s": events[-1]["t"],
            "dispatch": dispatch or {"agent": "a", "timeout_s": 1800},
            "events": events}


def wedged_probes(start=1200, step=300, count=4, final_age=WEDGE_AGE_CONFIRM_S):
    out = []
    for i in range(count):
        t = start + i * step
        age = final_age - (count - 1 - i) * step
        out.append(probe(t, 25.0, age, COM))
    return out


class TestCompletionSuppression(unittest.TestCase):
    def test_completion_suppresses_wedge_shaped_probes(self):
        # 2026-09-01/09-12 mechanism: post-success the process is idle, writes
        # silent, stacks identical — a wedge detector without a completion
        # check kills a finished job.
        w = window([OPEN, prog(300), done(900),
                    probe(1200, 0.2, 300, COM),
                    probe(1500, 0.1, 600, COM),
                    probe(1800, 0.1, 900, COM)])
        v = reference_decide(w)
        self.assertFalse(v["kill"])
        self.assertEqual(v["reason"], "completion_evidence")
        self.assertEqual(v["evidence"]["completion_t"], 900)
        self.assertFalse(v["measurement_degraded"])


class TestMeasurementAbstention(unittest.TestCase):
    def test_failed_probe_abstains(self):
        w = window([OPEN, prog(300), probe(600, 0, 0, ok=False)])
        v = reference_decide(w)
        self.assertFalse(v["kill"])
        self.assertTrue(v["measurement_degraded"])
        self.assertEqual(v["reason"], "measurement_degraded")

    def test_negative_cpu_abstains(self):
        w = window([OPEN, probe(300, -1.0, 30)])
        v = reference_decide(w)
        self.assertFalse(v["kill"])
        self.assertTrue(v["measurement_degraded"])

    def test_impossible_write_age_abstains(self):
        # Write age older than the dispatch itself (2026-09-17).
        w = window([OPEN, probe(300, 22.0, 999999)])
        v = reference_decide(w)
        self.assertFalse(v["kill"])
        self.assertTrue(v["measurement_degraded"])


class TestWedgeBoundaries(unittest.TestCase):
    def test_confirmed_wedge_kills_with_stack_evidence(self):
        w = window([OPEN, prog(300)] + wedged_probes())
        v = reference_decide(w)
        self.assertTrue(v["kill"])
        self.assertEqual(v["reason"], "confirmed_wedge")
        self.assertEqual(v["evidence"]["stack_frames"], COM)
        self.assertGreaterEqual(v["evidence"]["confirming_probes"], 2)

    def test_identical_stacks_required(self):
        probes = wedged_probes()
        probes[-1]["stack_frames"] = IO  # stack moved — not wedged
        w = window([OPEN, prog(300)] + probes)
        self.assertFalse(reference_decide(w)["kill"])

    def test_stack_evidence_required(self):
        probes = wedged_probes()
        for p in probes:
            p["stack_frames"] = None
        w = window([OPEN, prog(300)] + probes)
        self.assertFalse(reference_decide(w)["kill"])

    def test_silence_depth_required(self):
        probes = wedged_probes(final_age=WEDGE_AGE_CONFIRM_S - 300)
        w = window([OPEN, prog(300)] + probes)
        self.assertFalse(reference_decide(w)["kill"])

    def test_busy_cpu_required(self):
        # Silent writes + identical stacks + IDLE cpu = exited-but-unreaped,
        # not a wedge.
        probes = [probe(1200 + i * 300, 0.3, 600 + i * 300, COM)
                  for i in range(4)]
        w = window([OPEN, prog(300)] + probes)
        self.assertFalse(reference_decide(w)["kill"])

    def test_fresh_progress_suppresses_stale_looking_probes(self):
        # #105: the progress channel is primary evidence; a lagging probe
        # sample must not outvote fresh writes.
        w = window([OPEN, prog(300), probe(600, 25.0, 590), prog(900)])
        v = reference_decide(w)
        self.assertFalse(v["kill"])
        self.assertEqual(v["reason"], "progress_fresh")

    def test_wall_clock_is_not_a_kill_input(self):
        # 2026-08-25 / #108: past the MaxMinutes backstop with fresh progress
        # the only legal answer is no kill.
        w = window([OPEN, prog(3500), probe(3600, 4.0, 100)],
                   dispatch={"agent": "a", "timeout_s": 1800, "max_minutes": 45})
        self.assertFalse(reference_decide(w)["kill"])


if __name__ == "__main__":
    unittest.main()
