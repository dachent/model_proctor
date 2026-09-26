#!/usr/bin/env python3
"""Production replay: the corpus gates #105-#109 (TOOL-039, #110).

Skips wholesale until #106's predicate is importable through
production_bridge. Once it resolves, this suite is the acceptance gate: every
recorded incident must produce its authored verdict against PRODUCTION code.
"""
import sys
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

from production_bridge import load_production_decide  # noqa: E402
from replay_driver import check_expectations, load_case, run_case  # noqa: E402

CORPUS = _DIR / "corpus"
DECIDE = load_production_decide()


@unittest.skipUnless(DECIDE is not None,
                     "production kill predicate absent — lands with #106 (TOOL-035)")
class TestProductionReplay(unittest.TestCase):
    def test_all_cases_match_authored_verdicts(self):
        for path in sorted(CORPUS.glob("*.json")):
            case = load_case(path)
            with self.subTest(case_id=case["case_id"]):
                check_expectations(case, run_case(case, DECIDE))


if __name__ == "__main__":
    unittest.main()
