#!/usr/bin/env python3
"""Roster parity: roster.example.json must resolve to the runner's lane map.

`internal_lane_ids` translates a contract role (cheap/substantial/marathon) into
Kimi's literal lane id (flash/glm/k3); `lanes` then names the worker that lane
dispatches to. `runner.DEFAULT_AGENT_MAP` is the same binding expressed directly,
lane id -> agent. Nothing at runtime joins the two files, so `cheap` sat on
`ds-flash-worker` -- an id #73 renamed to `glm-flash-worker` on 2026-08-28 --
until someone diffed them by hand.

Parity is asserted against `DEFAULT_AGENT_MAP` ONLY, deliberately. The README
roster table is wider than the map on purpose (`gpt-oss-worker` and `k27-scout`
are documented there but are in neither the map nor the installed agents.json),
so a README parity test would be red on day one and would end up deleted or
weakened -- the drift-eraser this test exists to prevent.

Run: python -m unittest discover -s scripts/tests -v
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harnesses" / "kimi-code" / "runner"))
import runner  # noqa: E402

ROSTER = ROOT / "harnesses" / "kimi-code" / "roster.example.json"


def load_roster(path=ROSTER):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def role_binding_errors(lanes, internal, agent_map):
    """Every role must resolve, through its lane id, to the map's own worker.

    Returned rather than asserted so the checks below can be exercised against
    deliberately broken rosters.
    """
    errors = []
    for role in sorted(set(lanes) | set(internal)):
        if role not in lanes:
            errors.append(f"{role!r}: no lane binding")
            continue
        if role not in internal:
            errors.append(f"{role!r}: no internal lane id")
            continue
        lane = internal[role]
        if lane not in agent_map:
            errors.append(f"{role!r}: lane {lane!r} is not in DEFAULT_AGENT_MAP")
        elif lanes[role] != agent_map[lane]:
            errors.append(f"{role!r} -> lane {lane!r}: roster says "
                          f"{lanes[role]!r}, runner.py says {agent_map[lane]!r}")
    return errors


class RosterParity(unittest.TestCase):
    """The example roster and the runner's lane map are one binding."""

    def setUp(self):
        self.roster = load_roster()
        self.agent_map = runner.DEFAULT_AGENT_MAP
        self.lanes = self.roster["lanes"]
        self.internal = self.roster["internal_lane_ids"]

    def test_lane_agents_match_agent_map_values(self):
        self.assertEqual(
            sorted(self.lanes.values()), sorted(self.agent_map.values()),
            "roster.example.json lanes %s disagree with DEFAULT_AGENT_MAP %s"
            % (sorted(self.lanes.values()), sorted(self.agent_map.values())))

    def test_internal_lane_ids_cover_agent_map_keys(self):
        self.assertEqual(
            sorted(self.internal.values()), sorted(self.agent_map),
            "roster.example.json internal_lane_ids %s disagree with "
            "DEFAULT_AGENT_MAP keys %s"
            % (sorted(self.internal.values()), sorted(self.agent_map)))

    def test_each_role_resolves_to_its_lane_worker(self):
        """The pairwise check the two set comparisons above cannot make: a
        roster carrying the right ids on the wrong roles passes both of them."""
        self.assertEqual(
            role_binding_errors(self.lanes, self.internal, self.agent_map), [])

    def test_ladder_matches_lane_keys(self):
        self.assertEqual(sorted(self.roster["ladder"]), sorted(self.lanes))


class DriftIsDetectable(unittest.TestCase):
    """A test that only passes is not evidence; these prove it can fail."""

    def test_retired_lane_id_is_caught(self):
        roster = load_roster()
        roster["lanes"]["cheap"] = "ds-flash-worker"  # the id #73 retired
        self.assertTrue(role_binding_errors(roster["lanes"],
                                            roster["internal_lane_ids"],
                                            runner.DEFAULT_AGENT_MAP))

    def test_swapped_roles_are_caught(self):
        """Right ids, wrong roles: passes both set comparisons, must not pass here."""
        roster = load_roster()
        lanes = roster["lanes"]
        lanes["cheap"], lanes["substantial"] = (
            lanes["substantial"], lanes["cheap"])
        self.assertEqual(sorted(lanes.values()),
                         sorted(runner.DEFAULT_AGENT_MAP.values()))
        self.assertTrue(role_binding_errors(lanes, roster["internal_lane_ids"],
                                            runner.DEFAULT_AGENT_MAP))


if __name__ == "__main__":
    unittest.main()
