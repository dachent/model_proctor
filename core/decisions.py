#!/usr/bin/env python3
"""Shared decision core: the rules every harness must agree on.

model_proctor's harnesses differ in *architecture* — Kimi's runner owns dispatch and
spawns workers through the delegate wrapper, while ZCode owns subagent dispatch itself
and can only be gated in front of its `Agent` tool. Dispatch code cannot be shared.
The decisions behind dispatch are pure functions over data, and those live here, once.

Contract version 1. See policy/HARNESS_CONTRACT.md.

Python 3.10, standard library only.
"""
from __future__ import annotations

import hashlib
import re

CONTRACT_VERSION = 1

# ── lanes ───────────────────────────────────────────────────────────────
# Roles, not model names. A harness binds each role to a concrete worker in its
# roster; a harness with no cheap worker binds `cheap` to "self", meaning the
# orchestrator does the task and no dispatch is authorized.
LANES = ("cheap", "substantial", "marathon")

# Observable task features. Deliberately observable, not a judgement of difficulty:
# assessing "how hard is this" before solving it is the call a fast router is worst at.
FEATURES = ("bounded", "known_location", "objective_acceptance",
            "marathon", "open_ended", "multi_module", "unfamiliar_repo")


def lane_for(features):
    """Frozen task-start lane table. Returns (lane, reasons).

    This is `runner.py:lane_for` with role names substituted for model names —
    same predicates, same order, same feature set. The ordering matters: the
    marathon guard runs first, so a task that is both bounded and marathon-shaped
    goes to the marathon lane rather than being treated as trivially bounded.
    """
    if features.get("open_ended") or features.get("marathon"):
        return "marathon", ["open-ended/marathon task shape -> marathon worker"]
    if features.get("multi_module") or features.get("unfamiliar_repo"):
        return "substantial", ["substantial multi-module/unfamiliar-repo work"]
    if (features.get("bounded") and features.get("known_location")
            and features.get("objective_acceptance")):
        return "cheap", ["localized + bounded + objective acceptance -> cheap worker"]
    return "substantial", ["default: substantial work without a bounded signature"]


# ── failure fingerprints ────────────────────────────────────────────────
# A fingerprint identifies a failure *class*, so "the same failure three times"
# is decidable without a model's opinion. Normalization strips what varies run to
# run: addresses, timings, absolute paths, line numbers, bare numbers.
_NORMALIZERS = (
    (re.compile(r"0x[0-9a-fA-F]+"), "0xX"),
    (re.compile(r"\d+\.\d+s"), "Ts"),
    (re.compile(r"[A-Za-z]:[\\/][^\s:]+"), "PATH"),
    (re.compile(r"(?m)^/[^\s:]+"), "PATH"),
    (re.compile(r"line \d+"), "line N"),
    (re.compile(r"\d+"), "N"),
)

FINGERPRINT_TAIL_BYTES = 8000


def fingerprint(rc, stdout, stderr):
    """Normalized failure identity. Stable across paths, timings, addresses."""
    blob = (stdout + "\n" + stderr)[-FINGERPRINT_TAIL_BYTES:]
    for pattern, repl in _NORMALIZERS:
        blob = pattern.sub(repl, blob)
    digest = hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]
    return "rc%s:%s" % (rc, digest)


# ── stagnation and budgets ──────────────────────────────────────────────
STAGNATION_THRESHOLD = 3      # identical fingerprints before a lateral switch
DEFAULT_MAX_DISPATCHES = 3
DEFAULT_MAX_STAGNANT = 6      # identical fingerprints before terminal abort


def stagnation_run(fingerprints):
    """Length of the trailing run of identical fingerprints.

    Callers must pass a list that has already been cleared by any passing verify:
    a pass resets the run. Keeping this a pure function of the list — rather than
    letting each harness decide when to clear — is what stops the two sides
    disagreeing about whether a task is stagnant.
    """
    if not fingerprints:
        return 0
    last = fingerprints[-1]
    run = 0
    for value in reversed(fingerprints):
        if value != last:
            break
        run += 1
    return run


def next_action(rc, fingerprints, max_stagnant=DEFAULT_MAX_STAGNANT):
    """What the evidence says to do next. Computed, never guessed.

    Returns one of: accept | abort | lateral_switch | same_worker_repair.
    """
    if rc == 0:
        return "accept"
    run = stagnation_run(fingerprints)
    if run >= max_stagnant:
        return "abort"
    if run >= STAGNATION_THRESHOLD:
        return "lateral_switch"
    return "same_worker_repair"


def next_tier(ladder, current):
    """The only legal lateral switch: one step up the roster's ordered ladder."""
    if current not in ladder:
        return None
    i = ladder.index(current)
    return ladder[i + 1] if i + 1 < len(ladder) else None


# ── scope ───────────────────────────────────────────────────────────────
# Which paths a worker may change. Empty scope means unrestricted.
def glob_to_regex(pattern):
    """Path glob with real ** support.

    **  spans directory separators
    *   does not
    ?   one non-separator character
    """
    pattern = pattern.replace("\\", "/").strip()
    if pattern.endswith("/"):
        pattern += "**"
    out, i = [], 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i:i + 3] == "**/":
                out.append("(?:.*/)?")      # "a/**" also matches "a" itself
                i += 3
                continue
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        out.append("[^/]" if char == "?" else re.escape(char))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


def in_scope(rel, scope):
    for pattern in scope:
        p = pattern.replace("\\", "/").strip()
        if glob_to_regex(p).match(rel):
            return True
        # A bare directory name covers everything beneath it — but only when the
        # pattern has no glob characters, or this would defeat single-level "src/*"
        # by also matching "src/deep/x.py".
        if not any(ch in p for ch in "*?"):
            bare = p.rstrip("/")
            if bare and rel.lower().startswith(bare.lower() + "/"):
                return True
    return False


# ── verification surface ────────────────────────────────────────────────
# Files that change what a verifier *means* without changing the code under test.
# If one appears or changes after init, the verifier is no longer the exam that
# was agreed to, and verification is refused rather than run against a new one.
VERIFICATION_AFFECTING = frozenset({
    "conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml",
    "sitecustomize.py", "usercustomize.py",
})


def is_verification_affecting(rel):
    name = rel.rsplit("/", 1)[-1]
    return name in VERIFICATION_AFFECTING or name.endswith(".pth")


def scope_violations(scope, added, modified, deleted):
    """Paths changed outside the declared scope. Verification-affecting files are
    excluded here and reported separately, with a sharper error."""
    if not scope:
        return []
    changed = set(added) | set(modified) | set(deleted)
    return sorted(r for r in changed
                  if not is_verification_affecting(r) and not in_scope(r, scope))
