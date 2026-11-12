"""Pure tests for champion-select prefetch helpers (no client, no Tk)."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "overlay-py"))

import lcu  # noqa: E402


def session(game_id=1, bench=True, champions=(1, 2, 3, 4, 5), puuids=None):
    puuids = puuids or ["u%d" % index for index in range(5)]
    return {
        "gameId": game_id,
        "benchEnabled": bench,
        "bench": [10, 11] if bench else [],
        "team": [{"puuid": puuid, "championId": cid} for puuid, cid in zip(puuids, champions)],
    }


class ChampSelectHelperTests(unittest.TestCase):
    def test_key_is_puuid_order_independent(self):
        a = session(puuids=["a", "b", "c", "d", "e"])
        b = session(puuids=["e", "d", "c", "b", "a"])
        self.assertEqual(lcu.champ_select_key(a), lcu.champ_select_key(b))

    def test_key_changes_with_game(self):
        self.assertNotEqual(
            lcu.champ_select_key(session(game_id=1)),
            lcu.champ_select_key(session(game_id=2)),
        )

    def test_key_changes_with_puuids(self):
        self.assertNotEqual(
            lcu.champ_select_key(session(puuids=["a", "b", "c", "d", "e"])),
            lcu.champ_select_key(session(puuids=["a", "b", "c", "d", "f"])),
        )

    def test_aram_gate(self):
        self.assertTrue(lcu.is_aram_champ_select({"benchEnabled": True}))
        self.assertTrue(lcu.is_aram_champ_select({"bench": [1, 2]}))
        self.assertTrue(lcu.is_aram_champ_select({"benchChampionIds": [3]}))
        self.assertFalse(lcu.is_aram_champ_select({}))

    def test_ready(self):
        self.assertTrue(lcu.champ_select_ready(session()))
        self.assertFalse(lcu.champ_select_ready(session(champions=(1, 2, 3, 4, 0))))
        self.assertFalse(lcu.champ_select_ready({"team": [{"championId": 1}] * 4}))

    def test_bench_parsing(self):
        self.assertEqual(lcu.parse_bench([{"championId": 1}, {"championId": 0}, {"championId": 2}]), [1, 2])
        self.assertEqual(lcu.parse_bench([3, 0, 4]), [3, 4])
        self.assertEqual(lcu.parse_bench(None), [])


if __name__ == "__main__":
    unittest.main()
