#!/usr/bin/env python3
"""Wire-contract drift guard on the committed fixture (#3, #53, #54).

The fixture is a REDACTED REAL CAPTURE from the current roster (refreshed
2026-09-14 from a live glm-5p3-flash dispatch; allowlist-redacted by
evals/fixtures/wire/redact_wire.py — prompts, paths and identifiers never
reach it). These assertions close the silent-zero failure class the owner
named on #3: a kimi field rename or roster rotation must fail LOUDLY here,
not meter $0 downstream.

Guards:
  1. extract_log coverage: every event type recognized, no malformed lines.
  2. Usage buckets: all four token keys present on every usage.record
     (a rename drops the key), with inputOther and output non-zero.
  3. Pricing: every model in the wire is a key in evals/pricing.yaml.
  4. End-to-end metering: sum_usage_records + price_tokens on the fixture
     produce api_cost_usd > 0 — the exact assertion that catches the
     tokens_reported / all-zero-buckets / $0-cost failure mode.
"""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

EVALS = Path(__file__).resolve().parents[1]
REPO = EVALS.parents[0] if False else Path(__file__).resolve().parents[2]
FIXTURE = EVALS / "fixtures" / "wire" / "sample-wire.jsonl"
PRICING = EVALS / "pricing.yaml"
EXTRACT_LOG = REPO / "scripts" / "extract_log.py"
RUNNER = REPO / "harnesses" / "kimi-code" / "runner" / "runner.py"

USAGE_KEYS = ("inputOther", "output", "inputCacheRead", "inputCacheCreation")

spec = importlib.util.spec_from_file_location("runner_for_wire_guard",
                                              str(RUNNER))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def fixture_events():
    return [json.loads(l) for l in FIXTURE.read_text(encoding="utf-8")
            .splitlines() if l.strip()]


@unittest.skipUnless(FIXTURE.is_file(), "wire fixture not present")
class WireContractGuard(unittest.TestCase):

    def test_01_every_event_type_recognized_no_malformed(self):
        import shutil
        import tempfile
        outdir = Path(tempfile.mkdtemp(prefix="wire-guard-out-"))
        try:
            r = subprocess.run([sys.executable, str(EXTRACT_LOG),
                                str(FIXTURE), "--out", str(outdir)],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            manifest = outdir / "manifest.json"
            self.assertTrue(manifest.is_file(),
                            "extract_log wrote no manifest.json")
            doc = json.loads(manifest.read_text(encoding="utf-8"))
            files = doc.get("files") or []
            self.assertTrue(files, "manifest has no file entries")
            cov = files[0].get("coverage") or {}
            self.assertEqual(cov.get("records_unrecognized"), 0,
                             f"unrecognized types: "
                             f"{cov.get('unrecognized_types')}")
            self.assertEqual(cov.get("malformed_lines", 0), 0)
            self.assertGreater(cov.get("records_parsed", 0), 0)
        finally:
            shutil.rmtree(outdir, ignore_errors=True)

    def test_02_usage_buckets_all_keys_present_and_nonzero_core(self):
        usage = [e for e in fixture_events() if e.get("type") == "usage.record"]
        self.assertGreater(len(usage), 0, "fixture has no usage.record events")
        for e in usage:
            u = e.get("usage") or {}
            for k in USAGE_KEYS:
                self.assertIn(k, u,
                              f"bucket '{k}' missing — kimi renamed it? "
                              f"drift caught")
                self.assertIsInstance(u[k], (int, float))
            self.assertGreater(u["inputOther"], 0)
            self.assertGreater(u["output"], 0)

    def test_03_every_model_in_the_wire_is_priced(self):
        pricing = runner.load_pricing(str(PRICING))
        models = sorted({e.get("model") for e in fixture_events()
                         if e.get("type") == "usage.record" and e.get("model")})
        self.assertGreater(len(models), 0)
        unpriced = [m for m in models if m not in pricing]
        self.assertEqual(unpriced, [],
                         f"models in the wire without a pricing row "
                         f"(metering would be unknown): {unpriced}")

    def test_04_metering_end_to_end_is_nonzero(self):
        # The exact #3 assertion: not "did metering run" (non-null) but
        # "did it produce cost" — the all-zero-buckets failure mode fails
        # here loudly.
        records, totals = runner.sum_usage_records(str(FIXTURE))
        self.assertGreater(records, 0)
        by_model, total = runner.price_tokens(
            totals, runner.load_pricing(str(PRICING)))
        self.assertIsNotNone(total, "unpriced model in totals")
        self.assertGreater(total, 0.0,
                           "api_cost_usd == 0 on a real capture — the "
                           "silent-zero failure class (#3)")


if __name__ == "__main__":
    unittest.main()
