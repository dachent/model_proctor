#!/usr/bin/env python3
"""Corpus tests: validity, reference replay, byte-stable regeneration (TOOL-039, #110)."""
import sys
import tempfile
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

import gen_corpus  # noqa: E402
from reference_predicate import reference_decide  # noqa: E402
from replay_driver import check_expectations, load_case, run_case  # noqa: E402
from replay_schema import KNOWN_CLASSES  # noqa: E402

CORPUS = _DIR / "corpus"
EXPECTED_CASES = 10
EXPECTED_KILLS = 2  # only the 2026-09-25 control attempts may kill


def all_cases():
    return sorted(CORPUS.glob("*.json"))


class TestCorpusValidity(unittest.TestCase):
    def test_every_fixture_validates_and_matches_filename(self):
        paths = all_cases()
        self.assertEqual(len(paths), EXPECTED_CASES,
                         f"corpus should hold {EXPECTED_CASES} incidents")
        for path in paths:
            with self.subTest(file=path.name):
                case = load_case(path)
                self.assertEqual(path.name, case["case_id"] + ".json")

    def test_all_five_classes_present(self):
        classes = {load_case(p)["class"] for p in all_cases()}
        self.assertEqual(classes, KNOWN_CLASSES)

    def test_only_control_cases_expect_kill(self):
        kills = [load_case(p)["case_id"] for p in all_cases()
                 if load_case(p)["expect"]["kill"]]
        self.assertEqual(len(kills), EXPECTED_KILLS)
        for cid in kills:
            self.assertIn("2026-09-25", cid)


class TestReferenceReplay(unittest.TestCase):
    def test_all_cases_match_authored_verdicts(self):
        for path in all_cases():
            case = load_case(path)
            with self.subTest(case_id=case["case_id"]):
                check_expectations(case, run_case(case, reference_decide))


class TestRegeneration(unittest.TestCase):
    def test_corpus_regeneration_is_byte_stable(self):
        # core.autocrlf is workstation-dependent (true on the authoring
        # machine, no .gitattributes in-repo): compare with line endings
        # normalized on the checked-out side only. The generator itself
        # always writes LF bytes.
        with tempfile.TemporaryDirectory() as tmp:
            gen_corpus.main(tmp)
            for path in all_cases():
                regen = (Path(tmp) / path.name).read_bytes()
                committed = path.read_bytes().replace(b"\r\n", b"\n")
                self.assertEqual(
                    regen, committed,
                    f"{path.name}: regenerate with gen_corpus.py and commit "
                    f"the result")


if __name__ == "__main__":
    unittest.main()
