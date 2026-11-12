"""Unit tests for the teammate tag engine (no client, no network)."""

import json
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "overlay-py"))

import tagrules  # noqa: E402
import teammates  # noqa: E402

LOOKUPS = {
    "champion_tags": {
        1: ["Tank"],
        2: ["Mage"],
        3: ["Marksman"],
        4: ["Support", "Mage"],
        5: ["Assassin"],
        6: ["Fighter"],
        99: ["Mage"],
    },
    "champion_rank": {1: 150, 2: 20, 3: 30, 4: 160, 5: 40, 6: 50, 99: 170},
    "champion_key": {1: "champ1", 2: "champ2", 3: "champ3", 4: "champ4", 5: "champ5", 6: "champ6", 99: "champ99"},
    "champion_name": {1: "坦克", 2: "法师", 3: "射手", 4: "奶妈", 5: "刺客", 6: "战士", 99: "隐身法师"},
    "champion_total": 173,
    "augment_scores": {
        "champ2": {"1001": {"performance": 90}, "1002": {"performance": 70}},
    },
    "item_crit": {3031: 0.25, 3046: 0.25, 3094: 0.25, 6676: 0.25},
    "augment_crit": {1047: 50.0},
}


def player(champion_id=2, team_id=100, kills=5, deaths=5, assists=8,
           damage=20000, taken=20000, heal=2000, cc=20, gold=12000,
           items=None, augments=None, spells=None, win=True, longest=60):
    return {
        "puuid": "p-%s-%s" % (team_id, champion_id),
        "team_id": team_id,
        "champion_id": champion_id,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "damage": damage,
        "taken": taken,
        "heal": heal,
        "cc": cc,
        "gold": gold,
        "items": list(items or [0] * 7),
        "augments": list(augments or [0] * 6),
        "spells": list(spells or [4, 14]),
        "longest_time": longest,
        "win": win,
    }


def game(champion_id=2, index=0, win=True, self_stats=None, mates=None, duration=1200):
    self_p = player(champion_id=champion_id, win=win, **(self_stats or {}))
    mate_list = []
    for mate_index in range(4):
        default = dict(champion_id=6, kills=5, deaths=5, assists=8, damage=20000,
                       taken=20000, heal=2000, cc=20, gold=12000, win=win)
        if mates:
            default.update(mates)
        mate = player(**default)
        mate["puuid"] = "mate-%d-%d" % (index, mate_index)
        mate_list.append(mate)
    return {
        "game_id": 1000 + index,
        "queue_id": 2400,
        "map_id": 12,
        "game_mode": "KIWI",
        "duration": duration,
        "created_at": int((time.time() - index * 3600) * 1000),
        "self": self_p,
        "team": [self_p] + mate_list,
    }


def metrics_for(games):
    return tagrules.build_metrics(games, "p-100-2", LOOKUPS)


def ids(tags):
    return {tag["id"] for tag in tags}


class TagRuleTests(unittest.TestCase):
    def test_carry(self):
        games = [game(3, i, True, {"damage": 45000, "kills": 10, "assists": 10}) for i in range(12)]
        tags = tagrules.evaluate(metrics_for(games), {"level": 200, "full_team": True})
        self.assertIn("carry", ids(tags))

    def test_kill_hog_requires_eight_games(self):
        games = [game(2, i, True, {"kills": 10, "assists": 3, "damage": 15000}) for i in range(7)]
        tags = tagrules.evaluate(metrics_for(games), {"level": 200, "full_team": True})
        self.assertNotIn("kill_hog", ids(tags))

    def test_kill_hog_at_eight_games(self):
        games = [game(2, i, True, {"kills": 10, "assists": 3, "damage": 15000}) for i in range(8)]
        tags = tagrules.evaluate(metrics_for(games), {"level": 200, "full_team": True})
        self.assertIn("kill_hog", ids(tags))

    def test_kill_hog_hit_rate(self):
        strong = [game(2, i, True, {"kills": 10, "assists": 3, "damage": 15000}) for i in range(4)]
        weak = [game(2, i + 4, True, {"kills": 10, "assists": 10, "damage": 30000}) for i in range(6)]
        tags = tagrules.evaluate(metrics_for(strong + weak), {"level": 200, "full_team": True})
        self.assertNotIn("kill_hog", ids(tags))

    def test_wall(self):
        games = [
            game(1, i, True, {"taken": 45000, "damage": 12000, "deaths": 5,
                              "items": [3084, 3083, 3075, 3143, 3110, 3006]})
            for i in range(8)
        ]
        tags = tagrules.evaluate(metrics_for(games), {"level": 200, "full_team": True})
        self.assertIn("wall", ids(tags))
        self.assertIn("heartsteel", ids(tags))

    def test_healer_and_no_scratch(self):
        games = [
            game(4, i, i < 6, {"heal": 30000, "damage": 6000, "kills": 1, "assists": 18,
                               "items": [6617, 3504, 3107, 3222, 3190]})
            for i in range(10)
        ]
        tags = tagrules.evaluate(metrics_for(games), {"level": 200, "full_team": True})
        self.assertIn("heal_fountain", ids(tags))
        self.assertNotIn("scratch", ids(tags))

    def test_feeder(self):
        games = [
            game(2, i, False, {"deaths": 12, "kills": 2, "assists": 2, "damage": 15000})
            for i in range(10)
        ]
        tags = tagrules.evaluate(metrics_for(games), {"level": 200, "full_team": True})
        self.assertIn("feeder", ids(tags))

    def test_invisible(self):
        games = [
            game(99, i, False, {"damage": 8000, "taken": 7000, "kills": 1, "assists": 3})
            for i in range(10)
        ]
        tags = tagrules.evaluate(metrics_for(games), {"level": 200, "full_team": True})
        self.assertIn("invisible", ids(tags))

    def test_removed_build_and_reaper_tags(self):
        rule_ids = {rule["id"] for rule in tagrules.RULES}
        for removed in ("full_build", "naked", "reaper"):
            self.assertNotIn(removed, rule_ids)

    def test_k_head(self):
        games = [
            game(3, i, True, {"items": [6676, 3031, 3046, 3094, 0, 0, 0]})
            for i in range(8)
        ]
        tags = tagrules.evaluate(metrics_for(games), {"level": 200, "full_team": True})
        self.assertIn("k_head", ids(tags))

    def test_math_wizard(self):
        games = []
        for i in range(5):
            if i < 1:
                stats = {"augments": [1047, 0, 0, 0, 0, 0],
                         "items": [3031, 3046, 3094, 0, 0, 0, 0]}
            else:
                stats = {"augments": [0, 0, 0, 0, 0, 0],
                         "items": [3031, 3046, 3094, 0, 0, 0, 0]}
            games.append(game(3, i, True, stats))
        tags = tagrules.evaluate(metrics_for(games), {"level": 200, "full_team": True})
        self.assertIn("math_wizard", ids(tags))

    def test_stable_at_five_games(self):
        games = [
            game(1, i, True, {"taken": 40000, "damage": 12000, "deaths": 5,
                              "items": [3084, 3083, 3075, 0, 0, 0]})
            for i in range(5)
        ]
        tags = tagrules.evaluate(metrics_for(games), {"level": 200, "full_team": True})
        self.assertIn("heartsteel", ids(tags))
        self.assertIn("wall", ids(tags))

    def test_negative_tag_not_at_seven(self):
        games = [
            game(2, i, False, {"deaths": 12, "kills": 2, "assists": 2, "damage": 15000})
            for i in range(7)
        ]
        tags = tagrules.evaluate(metrics_for(games), {"level": 200, "full_team": True})
        self.assertNotIn("feeder", ids(tags))

    def test_afk_at_three_games(self):
        games = [
            game(2, i, False, {"damage": 500, "gold": 2000, "kills": 0, "assists": 0})
            for i in range(3)
        ]
        tags = tagrules.evaluate(metrics_for(games), {"level": 200, "full_team": True})
        self.assertIn("afk_suspect", ids(tags))
        self.assertIn("low_sample", ids(tags))

    def test_low_sample_only_data(self):
        games = [game(2, i, True, {"kills": 10, "assists": 3, "damage": 15000}) for i in range(4)]
        tags = tagrules.evaluate(metrics_for(games), {"level": 200, "full_team": True})
        self.assertEqual(ids(tags), {"low_sample"})

    def test_reasons(self):
        for reason in ("anonymous", "private", "unavailable", "no_aram", "low_sample"):
            tags = tagrules.evaluate({}, {"reason": reason})
            self.assertEqual(len(tags), 1)
            self.assertEqual(tags[0]["kind"], "data")

    def test_new_account(self):
        games = [game(2, i, True, {"kills": 10, "assists": 3, "damage": 15000}) for i in range(10)]
        tags = tagrules.evaluate(metrics_for(games), {"level": 20, "full_team": True, "new_account": True})
        self.assertIn("new_account", ids(tags))

    def test_smurf_requires_level(self):
        games = [game(3, i, True, {"damage": 45000, "kills": 10, "assists": 10}) for i in range(12)]
        tags_low = tagrules.evaluate(metrics_for(games), {"level": 50, "full_team": True})
        tags_high = tagrules.evaluate(metrics_for(games), {"level": 300, "full_team": True})
        self.assertIn("smurf", ids(tags_low))
        self.assertNotIn("smurf", ids(tags_high))

    def test_no_team_fallback_does_not_crash(self):
        games = []
        for index in range(8):
            item = game(1, index, True, {"taken": 45000, "damage": 12000, "deaths": 5,
                                         "items": [3084, 3083, 3075, 0, 0, 0]})
            item["team"] = None
            games.append(item)
        metrics = metrics_for(games)
        tags = tagrules.evaluate(metrics, {"level": 200, "full_team": False})
        self.assertIsInstance(tags, list)
        self.assertNotIn("wall", ids(tags))

    def test_exclusions(self):
        fake = [
            {"id": "wall", "kind": "behavior", "priority": 90, "excludes": ["paper_front"]},
            {"id": "paper_front", "kind": "behavior", "priority": 74, "excludes": ["wall"]},
            {"id": "carry", "kind": "behavior", "priority": 86, "excludes": []},
        ]
        selected = tagrules.apply_exclusions(fake)
        self.assertEqual([tag["id"] for tag in selected], ["wall", "carry"])

    def test_max_three_play_tags(self):
        games = [
            game(3, i, True, {"damage": 45000, "kills": 10, "assists": 10,
                              "items": [3031, 3006, 3072, 3085, 3036, 0]})
            for i in range(12)
        ]
        tags = tagrules.evaluate(metrics_for(games), {"level": 300, "full_team": True})
        play = [tag for tag in tags if tag["kind"] != "data"]
        self.assertLessEqual(len(play), 3)

    def test_confidence(self):
        high = metrics_for([game(3, i, True, {"damage": 45000}) for i in range(12)])
        mid = metrics_for([game(3, i, True, {"damage": 45000}) for i in range(8)])
        self.assertEqual(tagrules._confidence(high), "高")
        self.assertEqual(tagrules._confidence(mid), "中")


class NormalizationTests(unittest.TestCase):
    def _lcu_game(self):
        stats = {
            "kills": 7, "deaths": 4, "assists": 12,
            "totalDamageDealtToChampions": 22000, "totalDamageTaken": 18000,
            "totalHeal": 4000, "totalTimeCrowdControlDealt": 30,
            "goldEarned": 13000, "longestTimeSpentLiving": 90, "win": True,
            "item0": 3084, "item1": 3083, "item2": 0, "item3": 0, "item4": 0, "item5": 0, "item6": 0,
            "playerAugment1": 1001, "playerAugment2": 0, "playerAugment3": 0,
            "playerAugment4": 0, "playerAugment5": 0, "playerAugment6": 0,
        }
        participants = [{"participantId": i + 1, "teamId": 100 if i < 5 else 200,
                         "championId": 1, "spell1Id": 32, "spell2Id": 4, "stats": stats}
                        for i in range(10)]
        identities = [{"participantId": i + 1, "player": {"puuid": "u%d" % i}} for i in range(10)]
        return {"gameId": 1, "gameDuration": 1200, "gameCreation": 1700000000000,
                "queueId": 2400, "mapId": 12, "gameMode": "KIWI",
                "participants": participants, "participantIdentities": identities}

    def _sgp_game(self):
        participants = []
        for i in range(10):
            participants.append({
                "puuid": "u%d" % i, "teamId": 100 if i < 5 else 200, "championId": 2,
                "spell1Id": 32, "spell2Id": 4,
                "kills": 5, "deaths": 5, "assists": 5,
                "totalDamageDealtToChampions": 20000, "totalDamageTaken": 20000,
                "totalHeal": 2000, "totalTimeCrowdControlDealt": 20,
                "goldEarned": 12000, "longestTimeSpentLiving": 60, "win": True,
                "item0": 0, "item1": 0, "item2": 0, "item3": 0, "item4": 0, "item5": 0, "item6": 0,
                "playerAugment1": 0, "playerAugment2": 0, "playerAugment3": 0,
                "playerAugment4": 0, "playerAugment5": 0, "playerAugment6": 0,
            })
        return {"gameId": 2, "gameDuration": 1200, "gameCreation": 1700000000000,
                "queueId": 2400, "mapId": 12, "gameMode": "KIWI",
                "participants": participants}

    def test_lcu_nested_participants(self):
        game = teammates.normalize_game(self._lcu_game(), "u0")
        self.assertEqual(len(game["team"]), 10)
        self.assertEqual(game["self"]["puuid"], "u0")
        self.assertEqual(game["self"]["damage"], 22000)
        self.assertEqual(game["self"]["items"][0], 3084)

    def test_sgp_flat_participants(self):
        game = teammates.normalize_game(self._sgp_game(), "u3")
        self.assertEqual(len(game["team"]), 10)
        self.assertEqual(game["self"]["puuid"], "u3")
        self.assertEqual(game["self"]["champion_id"], 2)

    def test_sgp_summary_wrapper(self):
        inner = self._sgp_game()
        item = {"metadata": {"private": False, "match_id": "TJ101_2"}, "json": json.dumps(inner)}
        game = teammates.normalize_game(item, "u3")
        self.assertEqual(len(game["team"]), 10)
        self.assertEqual(game["self"]["puuid"], "u3")
        self.assertEqual(game["duration"], 1200)
        self.assertEqual(game["queue_id"], 2400)
        self.assertTrue(teammates._raw_is_aram(item))

    def test_list_single_participant(self):
        raw = self._lcu_game()
        raw["participants"] = raw["participants"][:1]
        raw["participantIdentities"] = raw["participantIdentities"][:1]
        game = teammates.normalize_game(raw, "u0")
        self.assertIsNone(game["team"])
        self.assertEqual(game["self"]["kills"], 7)


if __name__ == "__main__":
    unittest.main()
