"""Contract parity: the shared core and the Kimi runner must agree, exhaustively.

The lane table is a truth table over a small set of booleans, so it is enumerated
rather than sampled. Sampling is how two implementations of one rule stay green
while disagreeing on the inputs nobody wrote a case for.

Kimi keeps its own lane ids (flash/glm/k3). The contract names roles
(cheap/substantial/marathon). This asserts the mapping holds for every possible
feature vector, which is what makes "the contract documents Kimi's semantics"
a checkable claim rather than a comment.
"""
import itertools
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "harnesses" / "kimi-code" / "runner"))

import decisions  # noqa: E402
import runner as kimi_runner  # noqa: E402

# Kimi lane id -> contract role.
LANE_MAP = {"flash": "cheap", "glm": "substantial", "k3": "marathon"}


class LaneTableParity(unittest.TestCase):
    """Every feature vector, both implementations, no exceptions."""

    def test_exhaustive_agreement(self):
        names = decisions.FEATURES
        self.assertEqual(len(names), 7, "feature set changed; regenerate the table")
        mismatches = []
        for bits in itertools.product((False, True), repeat=len(names)):
            features = dict(zip(names, bits))
            core_lane, _ = decisions.lane_for(features)
            kimi_lane, _ = kimi_runner.lane_for(features)
            if LANE_MAP[kimi_lane] != core_lane:
                mismatches.append((features, kimi_lane, core_lane))
        self.assertEqual(
            mismatches, [],
            "core and Kimi disagree on %d of %d vectors; first: %s"
            % (len(mismatches), 2 ** len(names), mismatches[:1]))

    def test_table_covers_every_lane(self):
        seen = set()
        for bits in itertools.product((False, True), repeat=len(decisions.FEATURES)):
            lane, _ = decisions.lane_for(dict(zip(decisions.FEATURES, bits)))
            seen.add(lane)
        self.assertEqual(seen, set(decisions.LANES))

    def test_marathon_guard_runs_first(self):
        """A bounded task that is also marathon-shaped is NOT cheap.

        This is the ordering that a naive rewrite gets wrong, and getting it wrong
        inverts in the dangerous direction: a marathon task would be handed to the
        cheapest tier, or to no worker at all.
        """
        features = {"bounded": True, "known_location": True,
                    "objective_acceptance": True, "marathon": True}
        self.assertEqual(decisions.lane_for(features)[0], "marathon")
        self.assertEqual(kimi_runner.lane_for(features)[0], "k3")

    def test_lane_ids_are_not_silently_renamed(self):
        """Kimi's literal lane ids are keyed on by the production-runner refusal,
        the README refusal table and SKILL.md. Renaming them is a separate change
        routed through #26, so this pins them."""
        self.assertEqual(tuple(sorted(kimi_runner.LANES)), ("flash", "glm", "k3"))
        self.assertEqual(tuple(sorted(LANE_MAP.values())),
                         tuple(sorted(decisions.LANES)))


class BudgetParity(unittest.TestCase):
    def test_kimi_max_stagnant_is_representable(self):
        """Kimi's budget defaults must be expressible in the contract, even where
        the numbers differ — the contract carries a default, the roster overrides."""
        self.assertIn("max_stagnant", kimi_runner.DEFAULT_BUDGET)
        self.assertIsInstance(decisions.DEFAULT_MAX_STAGNANT, int)
        self.assertGreaterEqual(decisions.DEFAULT_MAX_STAGNANT,
                                decisions.STAGNATION_THRESHOLD)


if __name__ == "__main__":
    unittest.main()
