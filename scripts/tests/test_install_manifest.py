#!/usr/bin/env python3
"""The installer must not silently destroy uncommitted local work.

install.py rewrote every target unconditionally, so an installed `runner.py`
that had been extended locally — commands existing in no commit — was one
`python scripts/install.py` away from being replaced without a word. Nothing
could see the loss: the installed tree is the only copy, and a copy of the
repo is what the operator expected to get anyway.

The guard is a manifest of what the installer wrote (target -> sha256). A
target that differs from BOTH the repo source and its manifest hash is local
work, and the run refuses it unless `--force` says otherwise. The three
legitimate cases (absent target, target == repo, target == manifest) must keep
installing without a flag, or the guard would be unusable and get bypassed.

TOOL_DIR and USERPROFILE are redirected to a temp dir; nothing here touches
C:\\Tools or the real skill directory.

Run: python -m unittest discover -s scripts/tests -v
"""

import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIMI = ROOT / "harnesses" / "kimi-code"
INSTALL_PY = ROOT / "scripts" / "install.py"


def load_install(name="_install_manifest_under_test"):
    """Import install.py under its own name without executing main()."""
    spec = importlib.util.spec_from_file_location(name, INSTALL_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class InstallManifestGuardTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="install-manifest-")
        self.mod = load_install()
        self.mod.TOOL_DIR = Path(self.tmp) / "tools"
        self._home = os.environ.get("USERPROFILE")
        os.environ["USERPROFILE"] = str(Path(self.tmp) / "home")
        # A machine-local roster makes the ACL branch run; it is excluded from
        # the manifest by design, so keep its creation (or absence) exactly as
        # the existing install tests do and do not depend on it either way.
        self.agents_src = KIMI / "delegate" / "agents.json"
        self._made_agents = False
        if not self.agents_src.is_file():
            shutil.copy2(KIMI / "delegate" / "agents.example.json", self.agents_src)
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

    # -- helpers ---------------------------------------------------------

    def _run(self, *flags):
        """main() with stdout captured; returns (exit code, output)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.mod.main(list(flags))
        return code, buf.getvalue()

    def _install(self):
        code, out = self._run()
        self.assertEqual(code, 0, f"install failed:\n{out}")
        return out

    def _manifest(self):
        path = self.mod.TOOL_DIR / self.mod.MANIFEST_NAME
        return json.loads(path.read_text(encoding="utf-8"))

    def _target(self, name="runner.py"):
        return self.mod.TOOL_DIR / name

    # -- tests -----------------------------------------------------------

    def test_manifest_records_every_installed_target(self):
        self._install()
        manifest = self._manifest()
        names = sorted(name for name, _src in self.mod._targets())
        self.assertEqual(sorted(manifest), names)
        for name in names:
            self.assertEqual(manifest[name], self.mod._sha256(self._target(name)),
                             f"{name}: manifest hash does not match the file")
        self.assertNotIn("agents.json", manifest,
                         "agents.json is machine-local and must stay out of the guard: "
                         "a roster edit would otherwise read as a lost patch")

    def test_reinstall_is_clean(self):
        """(a) install, then install again: no flag, no refusal, same bytes."""
        self._install()
        before = {p.name: p.read_bytes() for p in self.mod.TOOL_DIR.iterdir()
                  if p.is_file()}
        out = self._install()
        self.assertNotIn("REFUSED", out)
        after = {p.name: p.read_bytes() for p in self.mod.TOOL_DIR.iterdir()
                 if p.is_file()}
        self.assertEqual(before, after, "re-install changed installed bytes")

    def test_stale_install_recorded_in_manifest_is_not_a_patch(self):
        """Differing from the repo is fine when the manifest says we wrote it.

        This is the ordinary case where the repo advanced and the install is
        one revision behind: refusing it would make every update need --force.
        """
        self._install()
        target = self._target()
        target.write_text("# older install, still the installer's own output\n",
                          encoding="utf-8")
        manifest = self._manifest()
        manifest["runner.py"] = self.mod._sha256(target)
        (self.mod.TOOL_DIR / self.mod.MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        code, out = self._run()
        self.assertEqual(code, 0, f"recorded re-install refused:\n{out}")
        self.assertEqual(self.mod._sha256(target),
                         self.mod._sha256(KIMI / "runner" / "runner.py"))

    def test_edited_target_refuses_and_is_preserved(self):
        """(b) an edit that no commit holds must survive a re-install."""
        self._install()
        target = self._target()
        patch = target.read_text(encoding="utf-8") + (
            "\n\ndef cmd_attest(args):\n"
            "    return 'local work that exists in no commit'\n")
        target.write_text(patch, encoding="utf-8")

        code, out = self._run()

        self.assertEqual(code, self.mod.REFUSED_LOCAL_PATCH,
                         f"edited target was not refused:\n{out}")
        self.assertNotEqual(code, 0)
        self.assertIn(str(target), out, "the refusal must name the file at risk")
        self.assertIn("cmd_attest", out, "the refusal must show the diff")
        self.assertIn("Commit or file the patch", out)
        self.assertIn("--force", out)
        self.assertEqual(target.read_text(encoding="utf-8"), patch,
                         "refused run still overwrote the local patch")

    def test_refusal_names_every_patched_file(self):
        self._install()
        for name in ("runner.py", "delegate.py"):
            path = self._target(name)
            path.write_text("# patched locally\n", encoding="utf-8")
        code, out = self._run()
        self.assertEqual(code, self.mod.REFUSED_LOCAL_PATCH, out)
        self.assertIn(str(self._target("runner.py")), out)
        self.assertIn(str(self._target("delegate.py")), out)

    def test_force_overwrites(self):
        """(c) --force is the only way past, and it restores the repo bytes."""
        self._install()
        target = self._target()
        target.write_text("# patched locally\n", encoding="utf-8")

        code, out = self._run("--force")

        self.assertEqual(code, 0, f"--force install failed:\n{out}")
        self.assertEqual(self.mod._sha256(target),
                         self.mod._sha256(KIMI / "runner" / "runner.py"),
                         "--force did not overwrite the patched file")
        self.assertEqual(self._manifest()["runner.py"], self.mod._sha256(target))

    def test_force_run_leaves_a_clean_install(self):
        """After --force the manifest describes what is on disk, so the next
        plain run is clean again."""
        self._install()
        self._target().write_text("# patched locally\n", encoding="utf-8")
        self.assertEqual(self._run("--force")[0], 0)
        code, out = self._run()
        self.assertEqual(code, 0, f"run after --force refused:\n{out}")

    def test_fresh_directory_installs_without_manifest(self):
        """No target, no manifest, no flag: the first run must just work."""
        self.assertFalse(self.mod.TOOL_DIR.exists())
        self._install()
        self.assertTrue((self.mod.TOOL_DIR / self.mod.MANIFEST_NAME).is_file())

    def test_missing_manifest_refuses_unknown_content(self):
        """A pre-guard install cannot be distinguished from a patch.

        Refusing is the fail-closed direction: the operator compares the file
        and passes --force deliberately, instead of losing it by default.
        """
        self._install()
        (self.mod.TOOL_DIR / self.mod.MANIFEST_NAME).unlink()
        self._target().write_text("# patched locally\n", encoding="utf-8")

        code, out = self._run()

        self.assertEqual(code, self.mod.REFUSED_LOCAL_PATCH, out)
        self.assertIn("No install-manifest.json exists yet", out)

    def test_corrupt_manifest_is_not_fatal(self):
        """A truncated manifest can only make the guard stricter."""
        self._install()
        (self.mod.TOOL_DIR / self.mod.MANIFEST_NAME).write_text("{not json",
                                                               encoding="utf-8")
        code, out = self._run()
        self.assertEqual(code, 0, f"clean install refused on a corrupt manifest:\n{out}")
        self.assertEqual(sorted(self._manifest()),
                         sorted(name for name, _src in self.mod._targets()),
                         "install did not rewrite the manifest")


if __name__ == "__main__":
    unittest.main()
