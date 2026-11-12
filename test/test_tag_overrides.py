"""Tag rule override tests (no client, no network)."""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "overlay-py"))

import tagrules  # noqa: E402


def carry_metrics(dmg=0.35, win=0.6, n=12):
    per_game = [{"dmgShare": dmg, "win": True} for _ in range(n)]
    return {
        "n": n,
        "winRate": win,
        "dmgShare": dmg,
        "per_game": per_game,
        "full_team": True,
    }


def tags(metrics, rules):
    return {tag["id"] for tag in tagrules.evaluate(metrics, {"level": 200, "full_team": True}, rules=rules)}


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="soraka-tag-")
        self.path = os.path.join(self.dir, "tag-overrides.json")
        tagrules._OVERRIDE_CACHE.update(path=None, mtime=None, rules=None)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        tagrules._OVERRIDE_CACHE.update(path=None, mtime=None, rules=None)

    def write(self, rules):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "rules": rules}, handle, ensure_ascii=False)

    def test_default_carry_is_strict(self):
        self.assertNotIn("carry", tags(carry_metrics(dmg=0.24, win=0.60), tagrules.RULES))
        self.assertIn("carry", tags(carry_metrics(dmg=0.35, win=0.60), tagrules.RULES))

    def test_override_changes_threshold(self):
        self.write({"carry": {"when": {"all": [
            {"metric": "dmgShare", "op": ">=", "value": 0.10},
        ]}, "per_game": {"all": [{"metric": "dmgShare", "op": ">=", "value": 0.10}]},
            "min_games": 5, "min_hit_rate": 0.2}})
        rules = tagrules.load_rules(self.dir)
        self.assertIn("carry", tags(carry_metrics(dmg=0.24, win=0.60), rules))

    def test_disabled_rule(self):
        self.write({"carry": {"enabled": False}})
        rules = tagrules.load_rules(self.dir)
        self.assertNotIn("carry", tags(carry_metrics(dmg=0.35, win=0.60), rules))

    def test_broken_file_falls_back(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        rules = tagrules.load_rules(self.dir)
        self.assertEqual([r["id"] for r in rules], [r["id"] for r in tagrules.RULES])


if __name__ == "__main__":
    unittest.main()
