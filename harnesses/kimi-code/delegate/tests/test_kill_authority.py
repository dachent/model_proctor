#!/usr/bin/env python3
"""Kill-site audit (TOOL-036, issue #107): the registry is the law.

Every kill-primitive occurrence in scoped production files must match
killauthority.KILL_SITES exactly; source drift in either direction fails the
suite. Authority-class files (expected=None) are exempt from counts and are
guarded by literal markers instead.

Run: python -m unittest discover -s delegate/tests -v
"""

import re
import sys
import unittest
from pathlib import Path

_DELEGATE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DELEGATE_DIR))

import killauthority  # noqa: E402

REPO_ROOT = _DELEGATE_DIR.parents[2]

FORBIDDEN = {
    "proc_kill": r"\.kill\(\s*\)",
    "proc_terminate": r"\.terminate\(\s*\)",
    "taskkill": r"\btaskkill\b",
    "terminate_process": r"\bTerminateProcess\b",
    "os_kill": r"\bos\.kill\(",
}

VALID_CLASSIFICATIONS = {
    "authority", "custody_violation_documented", "external_readonly",
    "own_child_cleanup", "stdlib_implicit", "frozen_artifact", "policy_pure",
}


class TestKillSiteRegistry(unittest.TestCase):
    def test_registry_schema(self):
        for entry in killauthority.KILL_SITES:
            with self.subTest(site=entry["site"]):
                self.assertIn(entry["classification"], VALID_CLASSIFICATIONS)
                self.assertTrue(entry["file"])
                self.assertTrue(entry["note"])

    def test_registry_counts_match_source(self):
        for entry in killauthority.KILL_SITES:
            with self.subTest(site=entry["site"]):
                path = REPO_ROOT / entry["file"]
                self.assertTrue(path.is_file(), f"missing file: {entry['file']}")
                text = path.read_text(encoding="utf-8")
                for marker in entry.get("must_contain", []):
                    self.assertIn(marker, text,
                                  f"{entry['file']} lost marker {marker!r}")
                for marker in entry.get("must_not_contain", []):
                    self.assertNotIn(marker, text,
                                     f"{entry['file']} gained marker {marker!r}")
                expected = entry.get("expected")
                if expected is None:
                    continue  # authority-class: marker-guarded, not counted
                for name, pattern in FORBIDDEN.items():
                    actual = len(re.findall(pattern, text))
                    self.assertEqual(
                        actual, expected.get(name, 0),
                        f"{entry['file']}:{name} expected "
                        f"{expected.get(name, 0)}, found {actual} — re-survey "
                        f"and justify in KILL_SITES")


if __name__ == "__main__":
    unittest.main()
