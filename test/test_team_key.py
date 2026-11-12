"""Team labels from Live Client Data and LCU can differ."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "overlay-py"))

from teamutil import team_key  # noqa: E402


class TeamKeyTests(unittest.TestCase):
    def test_order_forms(self):
        for value in (100, "100", "ORDER", "TEAM_100"):
            self.assertEqual(team_key(value), "ORDER")

    def test_chaos_forms(self):
        for value in (200, "200", "CHAOS", "TEAM_200"):
            self.assertEqual(team_key(value), "CHAOS")


if __name__ == "__main__":
    unittest.main()
