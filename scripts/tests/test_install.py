#!/usr/bin/env python3
"""install.py completeness and re-runnability (#47).

Two defects no hermetic suite could see, because every suite runs from the
repo root and therefore never notices that the INSTALLED tree is incomplete:

  * pricing.yaml was never copied to TOOL_DIR, so the installed SKILL.md's
    step 7 -- `record ... --pricing evals/pricing.yaml`, a relative path --
    failed for any leader not sitting in the repo root. That is the entire
    point of installing to C:\\Tools\\model-proctor. Silent loss of cost
    accounting is the same failure class as the Phase 2 tokens_reported null.
  * install.py advertises "(idempotent)" but ACL-hardens agents.json down to
    R for the current user, so its own second run hit PermissionError on the
    unconditional copy2.

TOOL_DIR is monkeypatched to a temp dir, so these never touch a real install.

Run: python -m unittest discover -s scripts/tests -v
"""

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIMI = ROOT / "harnesses" / "kimi-code"   # Kimi harness sources
INSTALL_PY = ROOT / "scripts" / "install.py"
SKILL_MD = KIMI / "skill" / "model-proctor" / "SKILL.md"

_IS_WINDOWS = sys.platform == "win32"


def load_install():
    """Import install.py under its own name without executing main()."""
    spec = importlib.util.spec_from_file_location("_install_under_test",
                                                  INSTALL_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class InstallCompletenessTest(unittest.TestCase):
    """Everything the installed skill tells a leader to run must be installed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="install-test-")
        self.mod = load_install()
        self.mod.TOOL_DIR = Path(self.tmp) / "tools"
        # Keep skills out of the real %USERPROFILE%.
        self._home = os.environ.get("USERPROFILE")
        os.environ["USERPROFILE"] = str(Path(self.tmp) / "home")

    def tearDown(self):
        if self._home is not None:
            os.environ["USERPROFILE"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sources_for_every_required_file_exist_in_repo(self):
        """Guards the copy list itself: a rename must not silently drop a file."""
        for base, subdir, name in self.mod.RUNNER_FILES:
            self.assertTrue((base / subdir / name).is_file(),
                            f"RUNNER_FILES names a missing source: {subdir}/{name}")
        for name in self.mod.DELEGATE_FILES:
            self.assertTrue((KIMI / "delegate" / name).is_file(),
                            f"DELEGATE_FILES names a missing source: {name}")

    def test_pricing_yaml_is_installed(self):
        # Assert the artefact, not the return code: the defect is a missing
        # file, and a test that fails on main()'s return value would go green
        # for the wrong reason.
        self.mod.main()
        self.assertTrue((self.mod.TOOL_DIR / "pricing.yaml").is_file(),
                        "record --pricing cannot resolve outside the repo root")

    def test_required_after_install_all_present(self):
        self.mod.main()
        for name in self.mod.REQUIRED_AFTER_INSTALL:
            self.assertTrue((self.mod.TOOL_DIR / name).is_file(), name)

    def test_main_reports_incomplete_install(self):
        """The completeness check must actually gate the exit code."""
        self.assertEqual(self.mod.main(), 0)
        (self.mod.TOOL_DIR / "pricing.yaml").unlink()
        self.mod.RUNNER_FILES = [t for t in self.mod.RUNNER_FILES
                                 if t[-1] != "pricing.yaml"]
        self.assertEqual(self.mod.main(), 1,
                         "a missing required file must fail the install")

    def test_skill_referenced_paths_are_installed(self):
        """Every C:/Tools/model-proctor/<file> the skill names must exist.

        This is the assertion that would have caught the pricing.yaml gap: it
        reads the shipped SKILL.md rather than a hand-maintained list.
        """
        self.mod.main()
        text = SKILL_MD.read_text(encoding="utf-8")
        import re
        named = set(re.findall(r"C:/Tools/model-proctor/([A-Za-z0-9_.-]+)", text))
        self.assertTrue(named, "SKILL.md names no installed paths — check the regex")
        for name in sorted(named):
            self.assertTrue((self.mod.TOOL_DIR / name).is_file(),
                            f"SKILL.md tells the leader to run {name}, "
                            f"which install.py does not install")


class InstallRerunTest(unittest.TestCase):
    """The second run must succeed as the same non-elevated user."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="install-rerun-")
        self.mod = load_install()
        self.mod.TOOL_DIR = Path(self.tmp) / "tools"
        self._home = os.environ.get("USERPROFILE")
        os.environ["USERPROFILE"] = str(Path(self.tmp) / "home")
        # A machine-local roster is normally untracked; synthesise one so the
        # ACL-hardening branch actually runs.
        self.agents_src = KIMI / "delegate" / "agents.json"
        self._made_agents = False
        if not self.agents_src.is_file():
            shutil.copy2(KIMI / "delegate" / "agents.example.json",
                         self.agents_src)
            self._made_agents = True

    def tearDown(self):
        if self._home is not None:
            os.environ["USERPROFILE"] = self._home
        if self._made_agents:
            try:
                self.agents_src.unlink()
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _hashes(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.mod.TOOL_DIR.iterdir()) if p.is_file()}

    @unittest.skipUnless(_IS_WINDOWS, "icacls hardening is Windows-only")
    def test_hardened_roster_stays_readable(self):
        """The hardening must not lock out the account the delegate runs as.

        `(OI)(CI)` are inherit flags: on a FILE they produce inherit-only ACEs
        that grant nothing to the file itself. icacls still reports success,
        but the resulting ACL is empty and delegate.py cannot load its own
        roster. Verified directly against icacls before this test was written.
        """
        self.assertEqual(self.mod.main(), 0, "first install failed")
        dst = self.mod.TOOL_DIR / "agents.json"
        self.assertTrue(dst.is_file(), "ACL branch did not run")
        try:
            dst.read_bytes()
        except PermissionError:
            self.fail("installed agents.json is unreadable by the current "
                      "user; the delegate could not load its roster")

    @unittest.skipUnless(_IS_WINDOWS, "icacls hardening is Windows-only")
    def test_hardened_roster_is_not_writable(self):
        """...and the hardening must still actually harden."""
        self.mod.main()
        dst = self.mod.TOOL_DIR / "agents.json"
        with self.assertRaises(PermissionError,
                               msg="roster is writable — hardening ineffective"):
            with open(dst, "ab") as f:
                f.write(b"x")

    @unittest.skipUnless(_IS_WINDOWS, "icacls hardening is Windows-only")
    def test_second_run_succeeds_and_is_outcome_identical(self):
        self.assertEqual(self.mod.main(), 0, "first install failed")
        first = self._hashes()
        self.assertIn("agents.json", first, "ACL branch did not run")

        # Previously raised PermissionError here: the first run left the
        # current user with R on agents.json and the copy is unconditional.
        self.assertEqual(self.mod.main(), 0, "second install failed")
        self.assertEqual(self._hashes(), first,
                         "install is not idempotent in outcome")


class RunEvalPathTest(unittest.TestCase):
    """The 2026-08-25 rename sweep missed evals/run_eval.py."""

    def test_runs_base_uses_post_rename_path(self):
        text = (ROOT / "evals" / "run_eval.py").read_text(encoding="utf-8")
        line = [l for l in text.splitlines() if l.startswith("RUNS_BASE")]
        self.assertEqual(len(line), 1, "RUNS_BASE assignment not found")
        self.assertIn("model-proctor", line[0])
        self.assertNotIn("kimi-router", line[0])


if __name__ == "__main__":
    unittest.main()
