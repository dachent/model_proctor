#!/usr/bin/env python3
"""The kimi wire contract, pinned against a real redacted capture (#53).

Every cost figure this repo has ever reported comes from wire.jsonl, and until
now nothing in the suite read a real one. test_s6_metering_reconciliation
cross-checks extract_log against sum_usage_records, but on synthesised events,
so it cannot see upstream drift.

The failure this guards is quiet, not loud. sum_usage_records accumulates with
`bucket[k] += usage.get(k, 0)`, so if kimi renames a usage field you get
usage_records > 0, every token bucket 0, and api_cost_usd == 0.0 -- a number,
not None. Any check of the form "did metering produce a value?" passes while
every downstream cost silently becomes zero. That is the Phase 2
tokens_reported failure through a different door; that one at least produced
null.

The fixture is a real session, whitelist-redacted by
evals/fixtures/wire/redact_wire.py. Refresh it on kimi-CLI version bumps
(#3, #54) -- a committed capture pins the PARSING contract and cannot detect
drift in a kimi newer than itself.

Run: python -m unittest discover -s scripts/tests -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "evals" / "fixtures" / "wire" / "sample-wire.jsonl"
PRICING = ROOT / "evals" / "pricing.yaml"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "runner"))
import extract_log  # noqa: E402
import runner  # noqa: E402

# Only three of the four buckets carry data in practice: real captures show
# inputCacheCreation at 0 throughout. Asserting all four non-zero would fail
# against genuine data, so the contract is "these three move".
LOAD_BEARING_BUCKETS = ("inputOther", "inputCacheRead", "output")


class FixturePresenceTest(unittest.TestCase):
    def test_fixture_and_redactor_are_committed(self):
        self.assertTrue(FIXTURE.is_file(), "the wire fixture is the whole point")
        self.assertTrue((FIXTURE.parent / "redact_wire.py").is_file(),
                        "the redactor must ship so refreshing is reproducible")


class CoverageTest(unittest.TestCase):
    """extract_log's frozen enumeration must cover a current kimi session."""

    def setUp(self):
        self.facts, self.cov = extract_log.extract_file(str(FIXTURE))

    def test_no_unrecognized_records(self):
        self.assertEqual(
            self.cov["records_unrecognized"], 0,
            f"kimi emits event types this build does not know: "
            f"{self.cov['unrecognized_types']}. Add them to KNOWN_TOP_LEVEL "
            f"after confirming they are real, and refresh the fixture.")

    def test_nothing_malformed_and_everything_parsed(self):
        self.assertEqual(self.cov["malformed_lines"], 0)
        self.assertEqual(self.cov["records_parsed"], self.cov["lines_total"])

    def test_capture_is_broad_enough_to_be_worth_pinning(self):
        types = {json.loads(l)["type"]
                 for l in FIXTURE.read_text(encoding="utf-8").splitlines() if l.strip()}
        self.assertGreaterEqual(len(types), 15,
                                "a capture this narrow would not detect much")
        self.assertIn("usage.record", types)


class MeteringTest(unittest.TestCase):
    """The path every cost number in this repo travels."""

    def setUp(self):
        self.n, self.totals = runner.sum_usage_records(str(FIXTURE))

    def test_usage_records_found(self):
        self.assertGreater(self.n, 0)

    def test_token_buckets_are_non_zero_not_merely_present(self):
        for model, buckets in self.totals.items():
            for key in LOAD_BEARING_BUCKETS:
                self.assertGreater(
                    buckets.get(key, 0), 0,
                    f"{model}.{key} summed to zero. A renamed usage field "
                    f"produces exactly this: records found, tokens zero, cost "
                    f"0.0 rather than None.")

    def test_every_model_is_priced(self):
        pricing = runner.load_pricing(str(PRICING))
        missing = sorted(set(self.totals) - set(pricing))
        self.assertEqual(missing, [],
                         f"{missing} absent from pricing.yaml — price_tokens "
                         "returns None for the total, which is "
                         "indistinguishable from a metering failure")

    def test_cost_is_positive(self):
        pricing = runner.load_pricing(str(PRICING))
        _, total = runner.price_tokens(self.totals, pricing)
        self.assertIsNotNone(total, "unpriced model in the capture")
        self.assertGreater(total, 0)


class RenameDetectionTest(unittest.TestCase):
    """Prove the assertions above would actually catch a field rename."""

    def test_renamed_usage_field_zeroes_cost_and_is_caught(self):
        rows = []
        for line in FIXTURE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            evt = json.loads(line)
            if evt.get("type") == "usage.record" and "usage" in evt:
                # Simulate upstream renaming inputOther -> promptTokens.
                u = dict(evt["usage"])
                if "inputOther" in u:
                    u["promptTokens"] = u.pop("inputOther")
                evt["usage"] = u
            rows.append(evt)

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                         encoding="utf-8", newline="\n") as tf:
            for r in rows:
                tf.write(json.dumps(r) + "\n")
            drifted = tf.name

        try:
            n, totals = runner.sum_usage_records(drifted)
            # The tell-tale shape: records still found...
            self.assertGreater(n, 0)
            # ...but the bucket is silently zero...
            for buckets in totals.values():
                self.assertEqual(buckets.get("inputOther", 0), 0)
            # ...and cost is a NUMBER, not None, so a null-check passes.
            _, total = runner.price_tokens(
                totals, runner.load_pricing(str(PRICING)))
            self.assertIsNotNone(total,
                                 "the danger is precisely that this is not None")
            # The non-zero assertion is what catches it.
            with self.assertRaises(AssertionError):
                for buckets in totals.values():
                    self.assertGreater(buckets.get("inputOther", 0), 0)
        finally:
            Path(drifted).unlink(missing_ok=True)


class RedactionTest(unittest.TestCase):
    """The fixture is derived from a real operator session in a public repo."""

    ALLOWED_KEYS = {"type", "time", "model", "usage", "event", "name", "args",
                    "redacted", "inputOther", "output", "inputCacheRead",
                    "inputCacheCreation"}

    def _walk(self, obj, keys, strings):
        if isinstance(obj, dict):
            for k, v in obj.items():
                keys.add(k)
                self._walk(v, keys, strings)
        elif isinstance(obj, list):
            for v in obj:
                self._walk(v, keys, strings)
        elif isinstance(obj, str):
            strings.add(obj)

    def setUp(self):
        self.keys, self.strings = set(), set()
        for line in FIXTURE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                self._walk(json.loads(line), self.keys, self.strings)

    def test_no_unexpected_keys(self):
        self.assertEqual(self.keys - self.ALLOWED_KEYS, set(),
                         "an unexpected key means the whitelist leaked")

    def test_no_free_text_survived(self):
        """Every string must be a timestamp, an enum, or the model id."""
        leaked = [s for s in self.strings
                  if not s.startswith("2026-01-01T")          # synthetic clock
                  and not s.replace(".", "").replace("_", "").isalnum()
                  and "/" not in s]                            # model ids
        self.assertEqual(leaked, [], f"free text in the fixture: {leaked}")

    def test_no_filesystem_paths_or_identifiers(self):
        for s in self.strings:
            self.assertNotIn("\\", s)
            self.assertNotIn("C:", s)
            self.assertNotRegex(s, r"session_[0-9a-f]{8}")
            self.assertNotIn("BorisVaisman", s)

    def test_timestamps_are_synthetic(self):
        stamps = [s for s in self.strings if s.startswith("2026-")]
        self.assertTrue(stamps, "expected the synthetic clock")
        for s in stamps:
            self.assertTrue(s.startswith("2026-01-01T"),
                            "real capture times must not survive redaction")


if __name__ == "__main__":
    unittest.main()
