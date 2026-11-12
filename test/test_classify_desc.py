"""Description-based augment fallback tests (no client, no Tk, no network)."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "overlay-py"))

import classify_local  # noqa: E402


AUGMENTS = [
    {"id": 1, "nameZh": "终极循环往复", "descZh": "获得50终极技能急速。"},
    {"id": 2, "nameZh": "仆从大师", "descZh": "你的召唤物获得40%体型提升、生命值和伤害。"},
    {"id": 3, "nameZh": "泰坦的坚决", "descZh": "在承受或造成伤害时获得层数。每7层获得适应之力、护甲、魔抗、体型和韧性。"},
]


class DescriptionFallbackTests(unittest.TestCase):
    def test_exact_description(self):
        match = classify_local._match_description("获得50终极技能急速", AUGMENTS)
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], 1)

    def test_partial_description(self):
        match = classify_local._match_description("召唤物获得40%体型提升", AUGMENTS)
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], 2)

    def test_short_or_unrelated_text_is_rejected(self):
        self.assertIsNone(classify_local._match_description("急速", AUGMENTS))
        self.assertIsNone(classify_local._match_description("完全不相干的说明文字", AUGMENTS))


if __name__ == "__main__":
    unittest.main()
