#!/usr/bin/env python3
"""Shared task-file schema: strict type validation for every harness (#83 M0).

The task file is the leader-authored contract every command boundary trusts.
Before this module each harness checked presence and little else, so a string
"false" in `features` was truthy at the lane table and routed the task to the
cheap lane (A12), a non-string scope glob crashed the scope matcher, and a
string budget passed silently into arithmetic. Validation lives here once so
no harness re-derives it and none can accept what another refuses.

Strictness is deliberate: unknown feature keys are refused (a typo like
"is_bounded" silently degrades a task to the default lane), and bools are
never accepted where ints are expected (bool is an int subclass in Python).

Python 3.10, standard library only. Contract version 1 (#83 M0).
"""
from __future__ import annotations

TASK_SCHEMA_VERSION = 1

# Observable task features — the frozen set from decisions.py. A task may
# declare any subset; it may not invent new ones (typo guard).
KNOWN_FEATURES = frozenset((
    "bounded", "known_location", "objective_acceptance",
    "marathon", "open_ended", "multi_module", "unfamiliar_repo",
))

# Budget fields this schema understands. Unknown budget keys pass through:
# budgets evolve faster than the schema, and an unknown key changes no
# decision the core makes.
_INT_BUDGET_FIELDS = ("max_dispatches", "max_stagnant")
_NUM_BUDGET_FIELDS = ("timeout_s", "max_preflight_age_s")


class TaskSchemaError(Exception):
    """A task file the harness must refuse. `field` names the offender."""

    def __init__(self, field, detail):
        super().__init__(f"{field}: {detail}")
        self.field = field
        self.detail = detail


def _require_str(value, field):
    if not isinstance(value, str) or not value.strip():
        raise TaskSchemaError(field, "must be a non-empty string")


def _require_str_list(value, field):
    if not isinstance(value, list) or not value:
        raise TaskSchemaError(field, "must be a non-empty array")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise TaskSchemaError(field, "array items must be non-empty strings")


def _require_bool(value, field):
    # A12: "false"/"true" strings and 0/1 ints are not booleans. Python's
    # truthiness made the string form silently true at the lane table.
    if not isinstance(value, bool):
        raise TaskSchemaError(
            field, f"must be a boolean, not {type(value).__name__}: {value!r}")


def _require_int(value, field, minimum=1):
    # bool is an int subclass; True must not pass as max_dispatches=1.
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TaskSchemaError(field, f"must be an integer >= {minimum}, not {value!r}")


def _require_num(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise TaskSchemaError(field, f"must be a positive number, not {value!r}")


def validate_task(task):
    """Validate a loaded task file in place; returns the task.

    Raises TaskSchemaError on the first refusal. Absent optional sections
    default legally: missing `features` is an empty set, missing `budget` is
    the harness's defaults to fill, and `schema_version` absent means the
    current version — old task files keep loading (migration compatibility,
    #83 M0).
    """
    if not isinstance(task, dict):
        raise TaskSchemaError("task", "must be a JSON object")
    version = task.get("schema_version", TASK_SCHEMA_VERSION)
    if version != TASK_SCHEMA_VERSION:
        raise TaskSchemaError(
            "schema_version",
            f"unsupported (have {version!r}, schema is {TASK_SCHEMA_VERSION})")
    _require_str(task.get("task_id"), "task_id")
    _require_str(task.get("prompt"), "prompt")
    _require_str_list(task.get("scope"), "scope")

    verifier = task.get("verifier")
    if not isinstance(verifier, dict):
        raise TaskSchemaError("verifier", "must be an object")
    _require_str_list(verifier.get("argv"), "verifier.argv")
    if verifier.get("seal") is not None:
        _require_str_list(verifier["seal"], "verifier.seal")

    # Absent features = the empty set (harness defaults fill it); the
    # validator does not insert what the leader did not write.
    if "features" in task:
        features = task["features"]
        if features is None:
            features = task["features"] = {}
        if not isinstance(features, dict):
            raise TaskSchemaError("features", "must be an object")
        for key, value in features.items():
            if key not in KNOWN_FEATURES:
                raise TaskSchemaError(
                    f"features.{key}",
                    f"unknown feature (known: {', '.join(sorted(KNOWN_FEATURES))})")
            _require_bool(value, f"features.{key}")

    if task.get("budget") is not None:
        budget = task["budget"]
        if not isinstance(budget, dict):
            raise TaskSchemaError("budget", "must be an object")
        for key in _INT_BUDGET_FIELDS:
            if key in budget:
                _require_int(budget[key], f"budget.{key}")
        for key in _NUM_BUDGET_FIELDS:
            if key in budget:
                _require_num(budget[key], f"budget.{key}")

    if task.get("lane") is not None:
        _require_str(task["lane"], "lane")
    if task.get("preflight_receipts") is not None:
        _require_str_list(task["preflight_receipts"], "preflight_receipts")
    return task
