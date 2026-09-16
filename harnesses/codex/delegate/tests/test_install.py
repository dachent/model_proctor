"""Contract tests for the explicit-destination Codex adapter installer.

The production break these tests catch is widening the install manifest to an
untracked/local artifact, or making a copied installation unusable outside the
source checkout.  Every destination in this module is a temporary directory.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


DELEGATE_DIR = Path(__file__).resolve().parent.parent
ROOT = DELEGATE_DIR.parents[2]
INSTALLER = DELEGATE_DIR / "install.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("_codex_install_under_test", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ExplicitDestinationInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="codex-install-test-"))
        self.destination = self.tmp / "adapter"
        if not INSTALLER.is_file():
            self.fail("explicit-destination Codex install.py is absent")
        self.installer = load_installer()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_manifest_is_limited_to_tracked_codex_adapter_artifacts(self):
        """A local config or executable path must never become install input."""
        tracked = set(subprocess.run(
            ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), "ls-files"],
            check=True, capture_output=True, text=True,
        ).stdout.splitlines())
        expected = {
            "harnesses/codex/delegate/delegate.py",
            "harnesses/codex/delegate/catalog.py",
            "harnesses/codex/delegate/runner_delegate.py",
            "harnesses/codex/delegate/runner-agent-map.json",
            "harnesses/codex/delegate/local-config.example.json",
            "harnesses/codex/delegate/README.md",
        }
        manifest = {str(Path("harnesses/codex/delegate") / name).replace("\\", "/")
                    for name in self.installer.INSTALL_MANIFEST}
        self.assertEqual(manifest, expected)
        self.assertTrue(manifest <= tracked)

    def test_explicit_temporary_destination_receives_a_runnable_adapter_set(self):
        """A missing copied catalog/example/docs file breaks a flat adapter install."""
        self.assertEqual(self.installer.main(["--destination", str(self.destination)]), 0)
        self.assertEqual(
            {path.name for path in self.destination.iterdir() if path.is_file()},
            {
                "delegate.py", "catalog.py", "runner_delegate.py",
                "runner-agent-map.json", "local-config.example.json", "README.md",
            },
        )
        runnable = subprocess.run(
            [sys.executable, str(self.destination / "delegate.py"), "--help"],
            cwd=self.destination, capture_output=True, text=True,
        )
        self.assertEqual(runnable.returncode, 0, runnable.stderr)
        self.assertIn("--transport", runnable.stdout)

    def test_destination_is_required(self):
        """Removing --destination must not recreate a default durable install."""
        with self.assertRaises(SystemExit) as error:
            self.installer.main([])
        self.assertEqual(error.exception.code, 2)

    def test_installed_readme_states_the_complete_shared_control_plane_boundary(self):
        """Stale copy instructions must not omit bridge artifacts or shared runner ownership."""
        readme = (DELEGATE_DIR / "README.md").read_text(encoding="utf-8")
        for artifact in (
            "delegate.py", "catalog.py", "runner_delegate.py", "runner-agent-map.json",
            "local-config.example.json", "README.md",
        ):
            with self.subTest(artifact=artifact):
                self.assertIn(f"`{artifact}`", readme)
        self.assertIn("`C:\\Tools\\model-proctor\\runner.py`", readme)
        self.assertIn("`C:\\Tools\\model-proctor\\task_schema.py`", readme)
        self.assertIn("does not copy or rewrite", readme)

    def test_existing_manifest_name_refuses_before_any_copy(self):
        """An existing delegate or later manifest file must survive unchanged."""
        for filename in (
            "delegate.py", "catalog.py", "runner_delegate.py", "runner-agent-map.json",
            "local-config.example.json", "README.md",
        ):
            with self.subTest(filename=filename):
                destination = self.tmp / filename.replace(".", "-")
                destination.mkdir()
                existing = destination / filename
                existing.write_bytes(b"existing operator-owned content\n")
                with self.assertRaises(ValueError):
                    self.installer.install(destination)
                self.assertEqual(existing.read_bytes(), b"existing operator-owned content\n")
                self.assertEqual([path.name for path in destination.iterdir()], [filename])


if __name__ == "__main__":
    unittest.main()
