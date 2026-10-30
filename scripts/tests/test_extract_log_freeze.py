#!/usr/bin/env python3
"""The extractor's freeze: a change to scripts/extract_log.py forces re-review.

``policy/leader-context-and-worker-integrity.md`` F1: "Extractor code is
verifier-class: hash-frozen per goal; changes force re-review." Until now that
was a sentence in a policy file with nothing enforcing it. This test is the
enforcement: the sha256 recorded in ``scripts/extract_log.freeze.json`` must
equal the file's, and the manifest must carry a review entry for that hash.

When this fails, the fix is NOT to paste the new hash in. It is to review what
the extractor now claims about a session — the fact rows every leader and every
trigger downstream trusts — and then add a review entry that says what changed
and what it was checked against.

Run: python -m unittest discover -s scripts/tests -v
"""

import hashlib
import json
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SOURCE = SCRIPTS / "extract_log.py"
MANIFEST = SCRIPTS / "extract_log.freeze.json"


class ExtractLogFreezeTestCase(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.actual = hashlib.sha256(SOURCE.read_bytes()).hexdigest()

    def test_manifest_names_this_file(self):
        self.assertEqual(self.manifest["path"], "scripts/extract_log.py")
        self.assertEqual(self.manifest["trust_class"], "verifier-class")

    def test_hash_matches_the_frozen_extractor(self):
        self.assertEqual(
            self.manifest["sha256"], self.actual,
            "scripts/extract_log.py changed without a re-review. Review the new "
            "extraction behavior, then record the change as a new entry in "
            "scripts/extract_log.freeze.json (reviews[]) and update sha256.")

    def test_the_frozen_hash_is_reviewed(self):
        reviewed = {entry["sha256_after"]
                    for entry in self.manifest["reviews"]}
        self.assertIn(self.manifest["sha256"], reviewed,
                      "the frozen hash has no review entry")

    def test_every_review_chain_links_to_the_previous_hash(self):
        chain = sorted(self.manifest["reviews"], key=lambda e: e["date"])
        for before, after in zip(chain, chain[1:]):
            self.assertEqual(after["sha256_before"], before["sha256_after"],
                             f"{after['date']} does not link to {before['date']}")


if __name__ == "__main__":
    unittest.main()
