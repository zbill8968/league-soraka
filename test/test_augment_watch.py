"""Pure tests for augment reroll screen signatures."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "overlay-py"))

import augment_watch  # noqa: E402


def sig(card0=0, card1=0, card2=0):
    return bytes([card0] * 256 + [card1] * 256 + [card2] * 256)


class AugmentWatchTests(unittest.TestCase):
    def test_single_card_change(self):
        a = sig(0, 0, 0)
        b = sig(80, 0, 0)
        self.assertTrue(augment_watch.changed(a, b))

    def test_no_change(self):
        self.assertFalse(augment_watch.changed(sig(20, 20, 20), sig(20, 20, 20)))

    def test_stable_tolerates_small_animation(self):
        self.assertTrue(augment_watch.stable(sig(20), sig(23)))
        self.assertFalse(augment_watch.stable(sig(20), sig(80)))

    def test_empty_is_not_changed(self):
        self.assertFalse(augment_watch.changed(b"", b""))


if __name__ == "__main__":
    unittest.main()
