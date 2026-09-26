#!/usr/bin/env python3
"""Shared timeout-stack sizing authority (#108 TOOL-037).

Every timeout guarding the same dispatch nests inner -> outer:

    delegate self-abort (--timeout)
      < delegate wall ceiling (timeout + kill grace + ~30s overhead)
      < runner wrapper breaker (timeout + RUNNER_BREAKER_MARGIN_S)
      < pilot case breaker (timeout + PILOT_BREAKER_MARGIN_S)

Before this module the margins were literals scattered across delegate.py
(docstring), runner.py (x4) and pilot.py, and budget.timeout_s sized BOTH
the dispatch and the verifier — a slow verifier read as a dispatch-scale
event. Margins are derived here once; harnesses import, never re-derive.

An outer breaker exists to kill a wedged inner layer, but it must give that
layer enough past its worst-case self-abort to write its named error
envelope (status=timeout, exit 124). A breaker that preempts the report
turns a named failure into the "silent death" tree-kill signature of #108.

TOOL-035/036 composition (Ruling, 2026-09-26): with stall_guard enabled the
delegate's deadline becomes a backstop and the evidence ladder may add up to
stall_guard.MAX_ADDED_SECONDS (60) past it, so the delegate's absolute worst
case is timeout + 60 (ladder) + 60 (grace) + 30 (overhead) = +150, which the
+120 runner breaker alone does not cover. Since TOOL-036 the runner's
breaker is a termination REQUEST point, not a kill: the runner waits
TERMINATION_REQUEST_WAIT_S (60) more for the delegate's own attributed
envelope, so the named-report guarantee holds out to +180 >= +150 + 30.
The breaker margin below therefore stays 120 and the ladder headroom lives
in the request window; do not "fix" the 120 without re-deriving both.

Python 3.10, standard library only. Pure functions; no I/O.
"""
from __future__ import annotations

# Worst-case seconds the delegate spends AFTER its self-abort fires: graceful
# taskkill, grace wait, force taskkill, job close, proc.wait, reader/stdin
# joins (delegate.py docstring lines 17-18).
DELEGATE_OVERHEAD_S = 30.0

# delegate._validate_config refuses default_kill_grace_seconds > 60. The
# runner sizes its breaker for the worst LEGAL grace because it does not
# read the delegate's config on the dispatch path.
KILL_GRACE_MAX_S = 60.0

# Minimum past the inner worst case an outer breaker must wait so the inner
# layer can report before the breaker fires.
MIN_REPORT_MARGIN_S = 30.0

# Runner wrapper breaker margin. Derived, not chosen:
# 60 + 30 + 30 = 120 — the justification for the historical "+ 120".
RUNNER_BREAKER_MARGIN_S = (KILL_GRACE_MAX_S + DELEGATE_OVERHEAD_S
                           + MIN_REPORT_MARGIN_S)

# Pilot (eval driver) breaker around one whole runner dispatch call.
PILOT_BREAKER_MARGIN_S = 300.0

# Verifier budget default now that budget.timeout_s no longer sizes it
# (the split that fixes the runner.py:1000 vs :1126 double-serve).
DEFAULT_VERIFY_TIMEOUT_S = 600.0


def delegate_ceiling_s(timeout_s, grace_s):
    """Worst-case wall clock one delegate dispatch can consume."""
    return float(timeout_s) + float(grace_s) + DELEGATE_OVERHEAD_S


def runner_breaker_s(timeout_s):
    """Runner wrapper deadline for a dispatch of timeout_s (worst-case grace)."""
    return float(timeout_s) + RUNNER_BREAKER_MARGIN_S


def pilot_breaker_s(timeout_s):
    """Pilot subprocess deadline around one runner dispatch of timeout_s."""
    return float(timeout_s) + PILOT_BREAKER_MARGIN_S


def stack_layers(timeout_s, grace_s):
    """The dispatch timeout stack, innermost first: (name, kind, seconds)."""
    return [
        ("delegate_self_abort", "self_abort", float(timeout_s)),
        ("delegate_ceiling", "ceiling", delegate_ceiling_s(timeout_s, grace_s)),
        ("runner_breaker", "breaker", runner_breaker_s(timeout_s)),
        ("pilot_breaker", "breaker", pilot_breaker_s(timeout_s)),
    ]


def validate_stack(layers, min_margin=MIN_REPORT_MARGIN_S):
    """Return the list of ordering violations; empty means the stack nests.

    A violation is any outer layer whose ceiling is not at least min_margin
    past the layer inside it — the configuration #108's preflight refuses.
    """
    violations = []
    for inner, outer in zip(layers, layers[1:]):
        if outer[2] < inner[2] + min_margin:
            violations.append(
                f"{outer[0]} ({outer[2]}s) must exceed {inner[0]} "
                f"({inner[2]}s) by at least {min_margin}s")
    return violations
