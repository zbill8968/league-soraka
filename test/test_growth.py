"""Level multiplier tests for growth augments."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "overlay-py"))

import classify_local  # noqa: E402
import growth  # noqa: E402
import scoring  # noqa: E402


class GrowthTests(unittest.TestCase):
    def setUp(self):
        self.data = growth.load(os.path.join(ROOT, "data"))

    def test_regular_growth_tiers(self):
        cases = [(3, 1.08), (6, 1.08), (7, 1.03), (10, 1.03),
                 (11, 0.97), (14, 0.97), (15, 0.92), (18, 0.92)]
        for level, expected in cases:
            self.assertAlmostEqual(growth.multiplier(self.data, 1152, level), expected, places=3)

    def test_phenomenal_evil_special(self):
        for level in (3, 6, 10):
            self.assertAlmostEqual(growth.multiplier(self.data, 1390, level), 1.03, places=3)
        self.assertAlmostEqual(growth.multiplier(self.data, 1390, 11), 0.97, places=3)
        self.assertAlmostEqual(growth.multiplier(self.data, 1390, 15), 0.92, places=3)

    def test_non_growth_and_unknown_level(self):
        self.assertEqual(growth.multiplier(self.data, 1002, 3), 1.0)
        self.assertEqual(growth.multiplier(self.data, 1152, None), 1.0)

    def test_death_loss_augments_are_not_weighted(self):
        for augment_id in (3132, 1353, 1373, 1136):
            self.assertEqual(growth.multiplier(self.data, augment_id, 3), 1.0)

    def test_classify_applies_level_multiplier(self):
        early = classify_local.classify(os.path.join(ROOT, "data"), "annie",
                                        ["钢化你心", "", ""], [None, None, None],
                                        "balanced", ["", "", ""], 3)["cards"][0]
        late = classify_local.classify(os.path.join(ROOT, "data"), "annie",
                                       ["钢化你心", "", ""], [None, None, None],
                                       "balanced", ["", "", ""], 16)["cards"][0]
        self.assertAlmostEqual(early["growthMultiplier"], 1.08, places=3)
        self.assertAlmostEqual(late["growthMultiplier"], 0.92, places=3)
        self.assertGreater(early["score"], early["baseScore"])
        self.assertLess(late["score"], late["baseScore"])

    def test_weighted_score_percentile(self):
        model = {"byAugment": {
            "1": {"performance": 60}, "2": {"performance": 80},
            "3": {"performance": 90}, "4": {"performance": 100},
        }}
        self.assertGreater(scoring.percentile_for_score(model, 97), 0.6)
        self.assertLess(scoring.percentile_for_score(model, 50), 0.1)


if __name__ == "__main__":
    unittest.main()
