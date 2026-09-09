"""Pricing contract tests (#83 TOOL-030 M3 slice).

Two failure classes these catch:

1. Flash/Fast row confusion — a flash-tier model priced from a fast-tier row.
   The 2026-08-28 glm-5p3-flash entry carried GLM 5.2 Fast's prices and ran
   flash-lane metering ~14x too high until corrected 2026-09-09
   (see evals/pricing.yaml).
2. A roster rotation that forgets pricing — every fireworks model the live
   delegate roster can dispatch must carry a price row, or metering silently
   records $0 (#3's proposed guard, applied at the roster level).

The exact-value pins are dated catalog checks, not timeless truths. When one
fails, re-verify against https://docs.fireworks.ai/serverless/pricing, correct
the row and its date — do not blind-update the test to make it pass.
"""
import json
import sys
import unittest
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVALS_DIR))

import meter  # noqa: E402

PRICING = meter.load_pricing(str(EVALS_DIR / "pricing.yaml"))
ROSTER = Path(r"C:/Tools/model-proctor/agents.json")

# Verified against the live page 2026-09-09 (standard tier).
GLM_5P3_FLASH = {"input": 0.15, "cached_input": 0.03, "output": 0.50}
DEEPSEEK_V4_PRO = {"input": 1.32, "cached_input": 0.044, "output": 3.96}


class FlashFastTiers(unittest.TestCase):
    def test_glm_5p3_flash_carries_the_flash_row_not_5p2_fast(self):
        self.assertEqual(PRICING["fireworks/glm-5p3-flash"], GLM_5P3_FLASH)

    def test_deepseek_v4_pro_standard_tier(self):
        self.assertEqual(PRICING["fireworks/deepseek-v4-pro"], DEEPSEEK_V4_PRO)

    def test_flash_tier_is_cheaper_than_its_base_model(self):
        # The class of the 2026-08-28 error: a flash model priced above its base.
        pairs = (("fireworks/glm-5p3-flash", "fireworks/glm-5p3"),
                 ("fireworks/deepseek-v4-flash", "fireworks/deepseek-v4-pro"))
        for flash, base in pairs:
            for field in ("input", "cached_input", "output"):
                self.assertLess(
                    PRICING[flash][field], PRICING[base][field],
                    f"{flash}.{field} must undercut {base}.{field}")


@unittest.skipUnless(ROSTER.is_file(), "machine-local delegate roster not present")
class RosterCoverage(unittest.TestCase):
    def test_every_rostered_fireworks_model_is_priced(self):
        roster = json.loads(ROSTER.read_text(encoding="utf-8"))
        models = set()
        for agent in roster.get("agents", {}).values():
            cmd = [str(t) for t in agent.get("command", [])]
            for i, tok in enumerate(cmd[:-1]):
                if tok == "-m" and cmd[i + 1].startswith("fireworks/"):
                    models.add(cmd[i + 1])
        unpriced = sorted(m for m in models if m not in PRICING)
        self.assertEqual(
            unpriced, [],
            f"roster models without a pricing row (metering would record $0): {unpriced}")


if __name__ == "__main__":
    unittest.main()
