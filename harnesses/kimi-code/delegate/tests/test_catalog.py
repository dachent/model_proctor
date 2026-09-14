#!/usr/bin/env python3
"""catalog.py tests (#96 / TOOL-031).

The catalog is kimi's live config joined with pricing. These tests use a
fixture KIMI_CODE_HOME and the repo's real (corrected) pricing.yaml: a
listed-and-priced model shows its true prices, an unpriced model is flagged
as meters-as-unknown, and an unreadable catalog is a failure, not a list.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_DELEGATE_DIR = Path(__file__).resolve().parent.parent
CATALOG = _DELEGATE_DIR / "catalog.py"


def make_home(tmp, models):
    home = Path(tmp) / "home"
    home.mkdir()
    lines = []
    for m in models:
        lines.append(f'[models."{m}"]\nprovider = "fireworks"\n')
    (home / "config.toml").write_text("".join(lines), encoding="utf-8")
    return str(home)


class CatalogCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="catalog-cli-")
        # fireworks/glm-5p3-flash is priced in the repo table ($0.15/$0.03/
        # $0.50, corrected 2026-09-09); fake/alpha is not priced at all.
        self.home = make_home(self.tmp, ["fake/alpha",
                                         "fireworks/glm-5p3-flash"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, home, *extra):
        env = dict(os.environ)
        env["KIMI_CODE_HOME"] = home
        env.pop("DELEGATE_CONFIG", None)
        r = subprocess.run([sys.executable, str(CATALOG), *extra],
                           capture_output=True, text=True, env=env,
                           timeout=60)
        return r.returncode, r.stdout, r.stderr

    def test_table_shows_prices_and_unknowns(self):
        rc, out, err = self._run(self.home)
        self.assertEqual(rc, 0, err)
        self.assertIn("fireworks/glm-5p3-flash", out)
        self.assertIn("0.15", out)              # corrected flash input price
        self.assertIn("fake/alpha", out)
        self.assertIn("UNPRICED", out)
        self.assertIn("meters as unknown", out)

    def test_json_lists_both_kinds(self):
        rc, out, _ = self._run(self.home, "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["catalog_size"], 2)
        by_model = {r["model"]: r for r in data["models"]}
        self.assertTrue(by_model["fireworks/glm-5p3-flash"]["priced"])
        self.assertEqual(by_model["fireworks/glm-5p3-flash"]["input_per_m"],
                         0.15)
        self.assertFalse(by_model["fake/alpha"]["priced"])
        self.assertEqual(by_model["fake/alpha"]["meters_as"], "unknown")

    def test_unreadable_catalog_fails_loudly(self):
        rc, out, err = self._run(os.path.join(self.tmp, "no-such-home"))
        self.assertEqual(rc, 2)
        self.assertIn("catalog unavailable", err)


if __name__ == "__main__":
    unittest.main()
