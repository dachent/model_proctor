"""ZCode pricing-twin contract tests (#84 / #83 TOOL-030 M3 slice).

The 2026-08-28 roster rotation moved the live lanes to glm-5p3 / glm-5p3-flash
but this twin file never gained those rows — any zcode-metered usage of the
current lanes would land in `unpriced_models`. It also carried fast-tier rows
priced from their base models, hiding the fast-serving premium.

Pins are dated catalog checks (2026-09-09, standard tier), not timeless
truths: when one fails, re-verify against
https://docs.fireworks.ai/serverless/pricing and correct the row and its date.
"""
import json
import unittest
from pathlib import Path

PRICING = json.loads(
    (Path(__file__).resolve().parents[1] / "zproctor_pricing.json").read_text(
        encoding="utf-8"))["custom:fireworks"]

# Verified against the live page 2026-09-09 (standard tier).
GLM_5P3 = {"input": 1.40, "cached": 0.26, "output": 4.40}
GLM_5P3_FLASH = {"input": 0.15, "cached": 0.03, "output": 0.50}
KIMI_K3_FAST = {"input": 4.50, "cached": 0.45, "output": 22.50}
GLM_5P2_FAST = {"input": 2.10, "cached": 0.21, "output": 6.60}


class PricingTwin(unittest.TestCase):
    def test_rotated_roster_models_are_priced(self):
        self.assertEqual(PRICING.get("glm-5p3"), GLM_5P3)
        self.assertEqual(PRICING.get("glm-5p3-flash"), GLM_5P3_FLASH)

    def test_flash_tier_is_cheaper_than_its_base(self):
        for field in ("input", "cached", "output"):
            self.assertLess(
                PRICING["glm-5p3-flash"][field], PRICING["glm-5p3"][field],
                f"glm-5p3-flash.{field} must undercut glm-5p3.{field}")

    def test_fast_serving_is_a_premium_not_a_discount(self):
        self.assertEqual(PRICING.get("kimi-k3-fast"), KIMI_K3_FAST)
        self.assertEqual(PRICING.get("glm-5p2-fast"), GLM_5P2_FAST)
        for field in ("input", "cached", "output"):
            self.assertGreater(
                PRICING["kimi-k3-fast"][field], PRICING["kimi-k3"][field],
                f"kimi-k3-fast.{field} must exceed kimi-k3.{field}")
            self.assertGreater(
                PRICING["glm-5p2-fast"][field], PRICING["glm-5p2"][field],
                f"glm-5p2-fast.{field} must exceed glm-5p2.{field}")


if __name__ == "__main__":
    unittest.main()
