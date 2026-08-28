#!/usr/bin/env python3
"""Roster ACL hardening against the case that occurs in the field (#63).

#47 fixed the `(OI)(CI)` bug that made the grant apply to nothing. The
corrected call works on a FRESH file and does not work on a file that already
carries permissive entries -- which is every previously-installed agents.json.

`/inheritance:r` removes INHERITED ACEs only. Explicit ones survive it, and
`/grant:r` replaces only the named principal's entry. Observed live after the
deploy: `NT AUTHORITY\\Authenticated Users:(M)` was still present and icacls
reported "Successfully processed 1 files".

The #47 test missed it because its fixture file inherits its ACL from the temp
directory, so `/inheritance:r` strips it correctly. These tests set an
EXPLICIT permissive ACE first -- the state that actually occurs.

Windows-only; skipped elsewhere.

Run: python -m unittest discover -s scripts/tests -v
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL_PY = ROOT / "scripts" / "install.py"
_IS_WINDOWS = sys.platform == "win32"


def load_install():
    spec = importlib.util.spec_from_file_location("_install_acl_under_test",
                                                  INSTALL_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(_IS_WINDOWS, "icacls is Windows-only")
class ExplicitAceTest(unittest.TestCase):
    """The field case: permissive ACEs already on the file, not inherited."""

    def setUp(self):
        self.m = load_install()
        self.tmp = tempfile.mkdtemp(prefix="acl-test-")
        self.f = Path(self.tmp) / "agents.json"
        self.f.write_text('{"agents":{}}', encoding="utf-8")
        self.principal = (f"{os.environ.get('USERDOMAIN','')}\\{os.getlogin()}"
                          if os.environ.get("USERDOMAIN") else os.getlogin())
        # Put an EXPLICIT permissive ACE on the file. This is what a
        # previously-installed roster looks like, and what /inheritance:r
        # cannot touch.
        self.m._icacls(str(self.f), "/grant", "*S-1-5-11:(M)")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _acl(self):
        return self.m._icacls(str(self.f)).stdout or ""

    def test_precondition_explicit_ace_is_present(self):
        """Guard the fixture: without this the test proves nothing."""
        self.assertIn("Authenticated Users", self._acl(),
                      "fixture failed to create the permissive ACE")

    def test_broad_principals_are_removed(self):
        list(self.m.harden_acl(self.f, self.principal))
        acl = self._acl()
        for name in ("Authenticated Users", "Everyone"):
            self.assertNotIn(name, acl,
                             f"{name} survived hardening — this is the #63 bug")

    def test_still_readable_by_the_account_the_delegate_runs_as(self):
        """The half that must not break: delegate loads this file."""
        list(self.m.harden_acl(self.f, self.principal))
        try:
            self.f.read_bytes()
        except PermissionError:
            self.fail("roster unreadable after hardening — delegate cannot "
                      "load its own config")

    def test_not_writable(self):
        list(self.m.harden_acl(self.f, self.principal))
        with self.assertRaises(PermissionError,
                               msg="roster writable — hardening ineffective"):
            with open(self.f, "ab") as fh:
                fh.write(b"x")

    def test_reports_success_only_when_actually_hardened(self):
        out = "\n".join(self.m.harden_acl(self.f, self.principal))
        self.assertIn("hardened", out)
        self.assertNotIn("WARNING", out)

    def test_idempotent_across_repeated_hardening(self):
        list(self.m.harden_acl(self.f, self.principal))
        out = "\n".join(self.m.harden_acl(self.f, self.principal))
        self.assertNotIn("WARNING", out, "second harden must not regress")
        self.assertNotIn("Authenticated Users", self._acl())


@unittest.skipUnless(_IS_WINDOWS, "icacls is Windows-only")
class VerificationTest(unittest.TestCase):
    """The step whose absence produced both #47 and #63."""

    def setUp(self):
        self.m = load_install()
        self.tmp = tempfile.mkdtemp(prefix="acl-verify-")
        self.f = Path(self.tmp) / "agents.json"
        self.f.write_text('{"agents":{}}', encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_warns_when_a_broad_principal_survives(self):
        """Simulate the removal failing: the report must not claim success."""
        real = self.m._icacls

        def fake(*args):
            # Drop the /remove:g call, leaving the permissive ACE in place.
            if "/remove:g" in args:
                class R:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                return R()
            return real(*args)

        self.m._icacls(str(self.f), "/grant", "*S-1-5-11:(M)")
        self.m._icacls = fake
        try:
            out = "\n".join(self.m.harden_acl(self.f, "BUILTIN\\Administrators"))
        finally:
            self.m._icacls = real
        self.assertIn("WARNING", out)
        self.assertIn("NOT hardened", out)

    def test_sids_are_used_not_localised_names(self):
        """Names differ by Windows display language; SIDs do not."""
        for sid in self.m._BROAD_SIDS:
            self.assertTrue(sid.startswith("*S-1-"), sid)

    def test_inheritance_r_alone_does_not_remove_an_explicit_ace(self):
        """Pins the mechanism behind #63.

        This is exactly the #47 call. Against an EXPLICIT permissive ACE it
        reports success and changes nothing, which is why /remove:g is
        required. If a future Windows makes /inheritance:r strip explicit ACEs
        too, this test fails and the /remove:g step can be reconsidered.
        """
        self.m._icacls(str(self.f), "/grant", "*S-1-5-11:(M)")
        principal = (f"{os.environ.get('USERDOMAIN','')}\\{os.getlogin()}"
                     if os.environ.get("USERDOMAIN") else os.getlogin())
        r = self.m._icacls(str(self.f), "/inheritance:r",
                           "/grant:r", "Administrators:F",
                           "/grant:r", f"{principal}:R")
        self.assertEqual(r.returncode, 0, "icacls reports success...")
        acl = self.m._icacls(str(self.f)).stdout or ""
        self.assertIn("Authenticated Users", acl,
                      "...while leaving the permissive ACE in place — the #63 "
                      "bug. If this ever stops being true, revisit harden_acl.")


if __name__ == "__main__":
    unittest.main()
