#!/usr/bin/env python3
"""Task-schema contract tests (#83 TOOL-030 M0).

core/task_schema.py is the single task-file policy every harness calls at its
command boundary. These tests pin its refusals directly; the kimi runner's
gate tests (harnesses/kimi-code/runner/tests/test_task_schema_gate.py) pin
that the runner actually refuses through it.

Anchor case (A12): a string "false" feature was truthy at the lane table and
routed real work to the cheap lane.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import task_schema  # noqa: E402


def base():
    return {"task_id": "t1", "prompt": "Fix the bug.",
            "scope": ["src/"],
            "verifier": {"argv": ["{python}", "-m", "pytest", "-q"]}}


class ValidTasks(unittest.TestCase):
    def test_minimal_task_passes(self):
        self.assertEqual(task_schema.validate_task(base()), base())

    def test_full_task_passes(self):
        t = base()
        t.update({"features": {"bounded": True, "known_location": False},
                  "budget": {"max_dispatches": 4, "max_stagnant": 3,
                             "timeout_s": 60.5, "max_preflight_age_s": 3600},
                  "lane": "flash",
                  "preflight_receipts": ["out/doctor.log"],
                  "verifier": {"argv": ["python", "check.py"], "seal": ["check.py"]},
                  "schema_version": 1})
        task_schema.validate_task(t)

    def test_absent_optionals_default_legally(self):
        # Migration compatibility (#83 M0): old task files keep loading.
        t = task_schema.validate_task(base())
        self.assertNotIn("features", t)   # harness defaults fill it


class Refusals(unittest.TestCase):
    def _refused(self, mutate, field):
        t = base()
        mutate(t)
        with self.assertRaises(task_schema.TaskSchemaError) as ctx:
            task_schema.validate_task(t)
        self.assertEqual(ctx.exception.field, field)

    def test_a12_string_boolean_feature_refused(self):
        self._refused(lambda t: t.update(features={"bounded": "false"}),
                      "features.bounded")

    def test_int_boolean_feature_refused(self):
        self._refused(lambda t: t.update(features={"bounded": 1}),
                      "features.bounded")

    def test_unknown_feature_key_refused(self):
        self._refused(lambda t: t.update(features={"is_bounded": True}),
                      "features.is_bounded")

    def test_features_not_object_refused(self):
        self._refused(lambda t: t.update(features=[True]), "features")

    def test_non_string_scope_item_refused(self):
        self._refused(lambda t: t.update(scope=["src/", 3]), "scope")

    def test_empty_scope_refused(self):
        self._refused(lambda t: t.update(scope=[]), "scope")

    def test_verifier_argv_shell_string_refused(self):
        self._refused(lambda t: t.update(verifier={"argv": "python check.py"}),
                      "verifier.argv")

    def test_verifier_seal_not_a_list_refused(self):
        self._refused(lambda t: t.update(
            verifier={"argv": ["python", "check.py"], "seal": "check.py"}),
            "verifier.seal")

    def test_string_budget_refused(self):
        self._refused(lambda t: t.update(budget={"max_dispatches": "4"}),
                      "budget.max_dispatches")

    def test_boolean_budget_refused(self):
        # bool is an int subclass; True must not pass as max_stagnant=1.
        self._refused(lambda t: t.update(budget={"max_stagnant": True}),
                      "budget.max_stagnant")

    def test_string_timeout_refused(self):
        self._refused(lambda t: t.update(budget={"timeout_s": "60"}),
                      "budget.timeout_s")

    def test_zero_timeout_refused(self):
        self._refused(lambda t: t.update(budget={"timeout_s": 0}),
                      "budget.timeout_s")

    def test_future_schema_version_refused(self):
        self._refused(lambda t: t.update(schema_version=2), "schema_version")

    def test_preflight_receipts_not_a_list_refused(self):
        self._refused(lambda t: t.update(preflight_receipts="out/doctor.log"),
                      "preflight_receipts")

    def test_non_string_task_id_refused(self):
        self._refused(lambda t: t.update(task_id=7), "task_id")

    def test_blank_prompt_refused(self):
        self._refused(lambda t: t.update(prompt="   "), "prompt")

    def test_lane_not_a_string_refused(self):
        self._refused(lambda t: t.update(lane=["flash"]), "lane")


if __name__ == "__main__":
    unittest.main()
