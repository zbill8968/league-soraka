"""Teammate play-style metrics and tag rules for ARAM: Mayhem.

Pure functions only: feed it normalized games (see ``teammates.normalize_game``)
and lookups (champion tags, ranks, augment scores), get back a tag list. No
network, no client access; the only optional I/O is load_rules() reading the
local tag-overrides.json, so the rule engine is easy to unit-test.

The rule table is deliberately data-like: ``when`` is a small condition tree
(``all`` / ``any`` / ``not`` / ``{metric, op, value}``). Behaviour tags also
carry a per-game condition so a single lucky game cannot earn a label.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time

# ---------------------------------------------------------------------------
# item / spell constants

HEARTSTEEL = 3084
WARMOG = 3083
COLLECTOR = 6676
MARK_SPELL = 32

CRIT_CLASSES = {"Marksman", "Fighter", "Assassin"}
# Known crit-rate augments; the catalog parser fills gaps, but a few descriptions
# use a "?" instead of a number, so keep the standard 25% fallback explicit.
CRIT_AUGMENT_IDS = {
    1047: 50.0, 1048: 25.0, 1077: 25.0, 1080: 25.0, 1092: 25.0,
    1118: 25.0, 1328: 25.0, 1336: 25.0, 1356: 25.0, 1415: 25.0,
    2009: 25.0, 2010: 25.0, 3123: 25.0,
}
# Fallback only; data/items.json (Data Dragon) supplies the full map at runtime.
CRIT_ITEMS = {
    3031: 0.25, 3033: 0.25, 3036: 0.25, 3046: 0.25, 3085: 0.25,
    3094: 0.25, 3508: 0.25, 6670: 0.20, 6671: 0.20, 6672: 0.25,
    6673: 0.25, 6675: 0.25, 6676: 0.25, 3039: 0.20,
}

TANK_ITEMS = {
    3084,  # Heartsteel
    3083,  # Warmog's Armor
    3075,  # Thornmail
    3068,  # Sunfire Aegis
    3143,  # Randuin's Omen
    3110,  # Frozen Heart
    3190,  # Locket of the Iron Solari
    3050,  # Zeke's Convergence
    3109,  # Knight's Vow
    3071,  # Black Cleaver (bruiser/tank-adjacent)
    3748,  # Titanic Hydra
    3181,  # Hullbreaker
}

SUPPORT_ITEMS = {
    3107,  # Redemption
    3222,  # Mikael's Blessing
    3050,  # Zeke's Convergence
    3190,  # Locket of the Iron Solari
    2065,  # Shurelya's Battlesong
    3109,  # Knight's Vow
    3504,  # Ardent Censer
    6617,  # Moonstone Renewer
    3011,  # Chemtech Putrifier (support component line)
}

DAMAGE_ITEMS = {
    3031,  # Infinity Edge
    3036,  # Lord Dominik's Regards
    3072,  # Bloodthirster
    3085,  # Runaan's Hurricane
    3006,  # Berserker's Greaves
    3020,  # Sorcerer's Shoes
    3157,  # Zhonya's Hourglass
    3089,  # Rabadon's Deathcap
    3135,  # Void Staff
    3165,  # Morellonomicon
    4645,  # Shadowflame
    6655,  # Luden's Companion
    6656,  # Everfrost
    6672,  # Kraken Slayer
    6673,  # Immortal Shieldbow
    6675,  # Navori Quickblades
    6676,  # The Collector
    6692,  # Eclipse
    6694,  # Serylda's Grudge
    6695,  # Serpent's Fang
    3153,  # Blade of the Ruined King
    3124,  # Guinsoo's Rageblade
    3046,  # Phantom Dancer
    3094,  # Rapid Firecannon
    3095,  # Stormrazor
    3508,  # Essence Reaver
    3027,  # Rod of Ages
    3116,  # Rylai's Crystal Scepter
    3151,  # Liandry's Torment
    3165,  # Morellonomicon (duplicate id is harmless)
}

# ---------------------------------------------------------------------------
# helpers


def _median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return float(vals[mid])
    return (float(vals[mid - 1]) + float(vals[mid])) / 2.0


def _mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _share(part, whole):
    if whole is None or whole <= 0:
        return None
    return float(part) / float(whole)


def _rate(part, minutes):
    if minutes <= 0:
        return None
    return float(part) / minutes


def _has(items, item_id):
    return item_id in (items or [])


def _count_items(items, pool):
    return sum(1 for item in (items or []) if item in pool)


def _item_count(items):
    # item0..item5 are the build; item6 is the trinket / role-bound item.
    return sum(1 for item in (items or [])[:6] if item)


def _champion_tags(lookups, champion_id):
    return set((lookups.get("champion_tags") or {}).get(int(champion_id or 0)) or [])


def _off_build(tags, items):
    damage = _count_items(items, DAMAGE_ITEMS)
    tank = _count_items(items, TANK_ITEMS)
    if "Tank" in tags and damage >= 3:
        return True
    if tags & {"Mage", "Marksman", "Assassin"} and tank >= 3:
        return True
    if "Support" in tags and damage >= 4:
        return True
    return False


def _augment_perf(lookups, champion_id, augments):
    key = (lookups.get("champion_key") or {}).get(int(champion_id or 0))
    table = (lookups.get("augment_scores") or {}).get(key) or {}
    scores = []
    for aug in augments or []:
        if not aug:
            continue
        rec = table.get(str(aug)) or table.get(aug)
        if isinstance(rec, dict) and isinstance(rec.get("performance"), (int, float)):
            scores.append(float(rec["performance"]))
    return _mean(scores)


def _same_team_participants(game, self_participant):
    team = game.get("team") or []
    if not team:
        return []
    team_id = self_participant.get("team_id")
    return [p for p in team if p.get("team_id") == team_id]


def _player_metrics(game, lookups):
    """One game's metrics for the tracked player, or None if no self entry."""
    self_p = game.get("self")
    if not isinstance(self_p, dict):
        return None
    duration = float(game.get("duration") or 0)
    minutes = max(1.0, duration / 60.0)
    items = list(self_p.get("items") or [])
    augments = list(self_p.get("augments") or [])
    spells = list(self_p.get("spells") or [])
    tags = _champion_tags(lookups, self_p.get("champion_id"))
    champion_id = int(self_p.get("champion_id") or 0)

    kills = int(self_p.get("kills") or 0)
    deaths = int(self_p.get("deaths") or 0)
    assists = int(self_p.get("assists") or 0)
    damage = float(self_p.get("damage") or 0)
    taken = float(self_p.get("taken") or 0)
    heal = float(self_p.get("heal") or 0)
    cc = float(self_p.get("cc") or 0)
    gold = float(self_p.get("gold") or 0)
    longest = self_p.get("longest_time")
    win = bool(self_p.get("win"))

    mates = _same_team_participants(game, self_p)
    has_team = len(mates) >= 4
    if has_team:
        team_kills = sum(int(p.get("kills") or 0) for p in mates)
        team_deaths = sum(int(p.get("deaths") or 0) for p in mates)
        team_assists = sum(int(p.get("assists") or 0) for p in mates)
        team_damage = sum(float(p.get("damage") or 0) for p in mates)
        team_taken = sum(float(p.get("taken") or 0) for p in mates)
        team_heal = sum(float(p.get("heal") or 0) for p in mates)
        team_cc = sum(float(p.get("cc") or 0) for p in mates)
        team_gold = sum(float(p.get("gold") or 0) for p in mates)
    else:
        team_kills = team_deaths = team_assists = 0
        team_damage = team_taken = team_heal = team_cc = team_gold = 0

    kill_share = _share(kills, team_kills)
    assist_share = _share(assists, team_assists)
    death_share = _share(deaths, team_deaths)
    dmg_share = _share(damage, team_damage)
    taken_share = _share(taken, team_taken)
    heal_share = _share(heal, team_heal)
    cc_share = _share(cc, team_cc)
    gold_share = _share(gold, team_gold)
    kp = _share(kills + assists, team_kills)

    rank = (lookups.get("champion_rank") or {}).get(champion_id)
    total_champs = int(lookups.get("champion_total") or 173)

    item_crit = lookups.get("item_crit") or CRIT_ITEMS
    crit_from_items = sum(float(item_crit.get(int(item), 0) or 0) for item in items)
    augment_crit = lookups.get("augment_crit") or CRIT_AUGMENT_IDS
    crit_from_augments = 0.0
    crit_augment = False
    for augment_id in augments[:3]:
        bonus = float(augment_crit.get(int(augment_id or 0), 0) or 0)
        if bonus:
            crit_augment = True
            crit_from_augments += bonus
    crit_chance = crit_from_items * 100.0 + crit_from_augments

    metrics = {
        "champion_id": champion_id,
        "win": win,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "damage": damage,
        "taken": taken,
        "heal": heal,
        "cc": cc,
        "gold": gold,
        "killsPerMin": _rate(kills, minutes),
        "deathsPerMin": _rate(deaths, minutes),
        "assistsPerMin": _rate(assists, minutes),
        "dmgPerMin": _rate(damage, minutes),
        "takenPerMin": _rate(taken, minutes),
        "healPerMin": _rate(heal, minutes),
        "goldPerMin": _rate(gold, minutes),
        "dmgPerKill": (damage / kills) if kills else None,
        "kda": (kills + assists) / max(1, deaths),
        "has_team": has_team,
        "dmgShare": dmg_share,
        "takenShare": taken_share,
        "healShare": heal_share,
        "ccShare": cc_share,
        "killShare": kill_share,
        "assistShare": assist_share,
        "deathShare": death_share,
        "goldShare": gold_share,
        "kp": kp,
        "killShareMinusDamageShare": (kill_share - dmg_share)
        if kill_share is not None and dmg_share is not None
        else None,
        "tankIndex": (taken_share - dmg_share)
        if taken_share is not None and dmg_share is not None
        else None,
        "supportShare": (heal_share + cc_share)
        if heal_share is not None and cc_share is not None
        else None,
        "itemsCount": _item_count(items),
        "heartsteel": _has(items, HEARTSTEEL),
        "warmog": _has(items, WARMOG),
        "collector": _has(items, COLLECTOR),
        "isKHeadClass": bool(tags & CRIT_CLASSES),
        "critAugment": crit_augment,
        "critChance": crit_chance,
        "mathWizard": bool(crit_augment and crit_chance >= 125.0),
        "tankItems": _count_items(items, TANK_ITEMS),
        "supportItems": _count_items(items, SUPPORT_ITEMS),
        "damageItems": _count_items(items, DAMAGE_ITEMS),
        "steelWarmog": _has(items, HEARTSTEEL) and _has(items, WARMOG),
        "offBuild": _off_build(tags, items),
        "mark": MARK_SPELL in spells,
        "augPerf": _augment_perf(lookups, champion_id, augments),
        "offMeta": (rank is not None and rank > total_champs * 0.7),
        "rank": rank,
        "longest": float(longest) if isinstance(longest, (int, float)) else None,
        "isTank": "Tank" in tags,
        "isMage": "Mage" in tags,
        "isMarksman": "Marksman" in tags,
        "isAssassin": "Assassin" in tags,
        "isSupport": "Support" in tags,
        "isFighter": "Fighter" in tags,
        # Enchanters such as Soraka are Mage+Support; they should not be judged
        # as "scratch damage" carries. A support-tagged mage is still a support.
        "isDamageClass": bool(tags & {"Mage", "Marksman", "Assassin"}) and "Support" not in tags,
        "created_at": int(game.get("created_at") or 0),
    }
    return metrics


def _rate_of(per_game, key):
    hits = [1 if g.get(key) else 0 for g in per_game]
    return (sum(hits) / len(hits)) if hits else None


def _median_of(per_game, key):
    return _median([g.get(key) for g in per_game])


def _mean_of(per_game, key):
    return _mean([g.get(key) for g in per_game])


def build_metrics(games, self_puuid=None, lookups=None):
    """Aggregate normalized games into one metrics dict for the rules."""
    lookups = lookups or {}
    per_game = []
    for game in games or []:
        metrics = _player_metrics(game, lookups)
        if metrics:
            per_game.append(metrics)
    n = len(per_game)
    if not n:
        return {
            "n": 0,
            "per_game": [],
            "full_team": False,
            "winRate": None,
            "full_team_games": 0,
        }

    now = time.time()
    created = [g.get("created_at") or 0 for g in per_game if g.get("created_at")]
    ordered = sorted(per_game, key=lambda g: g.get("created_at") or 0, reverse=True)
    wins = sum(1 for g in per_game if g.get("win"))
    games24h = sum(1 for g in per_game if g.get("created_at") and now - g["created_at"] / 1000.0 <= 86400)
    night = sum(
        1
        for g in per_game
        if g.get("created_at") and time.localtime(g["created_at"] / 1000.0).tm_hour in (0, 1, 2, 3, 4, 5)
    )
    loss_streak = 0
    for g in ordered:
        if g.get("win"):
            break
        loss_streak += 1
    win_streak = 0
    for g in ordered:
        if not g.get("win"):
            break
        win_streak += 1
    recent5 = ordered[:5]
    recent4 = ordered[:4]
    recent10 = ordered[:10]

    champ_counts = {}
    for g in per_game:
        cid = g.get("champion_id") or 0
        champ_counts[cid] = champ_counts.get(cid, 0) + 1
    top_cid, top_count = max(champ_counts.items(), key=lambda kv: kv[1]) if champ_counts else (0, 0)
    top_wins = sum(1 for g in per_game if g.get("champion_id") == top_cid and g.get("win"))

    team_games = sum(1 for g in per_game if g.get("has_team"))
    total_champs = int(lookups.get("champion_total") or 173)

    def class_rate(name):
        return sum(1 for g in per_game if g.get(name)) / n

    metrics = {
        "n": n,
        "wins": wins,
        "winRate": wins / n,
        "kda": _mean_of(per_game, "kda"),
        "killsPerMin": _mean_of(per_game, "killsPerMin"),
        "deathsPerMin": _mean_of(per_game, "deathsPerMin"),
        "assistsPerMin": _mean_of(per_game, "assistsPerMin"),
        "dmgPerMin": _mean_of(per_game, "dmgPerMin"),
        "takenPerMin": _mean_of(per_game, "takenPerMin"),
        "healPerMin": _mean_of(per_game, "healPerMin"),
        "goldPerMin": _mean_of(per_game, "goldPerMin"),
        "dmgShare": _median_of(per_game, "dmgShare"),
        "takenShare": _median_of(per_game, "takenShare"),
        "healShare": _median_of(per_game, "healShare"),
        "ccShare": _median_of(per_game, "ccShare"),
        "killShare": _median_of(per_game, "killShare"),
        "assistShare": _median_of(per_game, "assistShare"),
        "deathShare": _median_of(per_game, "deathShare"),
        "goldShare": _median_of(per_game, "goldShare"),
        "kp": _median_of(per_game, "kp"),
        "killShareMinusDamageShare": _median_of(per_game, "killShareMinusDamageShare"),
        "tankIndex": _median_of(per_game, "tankIndex"),
        "supportShare": _median_of(per_game, "supportShare"),
        "dmgPerKill": _median_of(per_game, "dmgPerKill"),
        "longestMedian": _median_of(per_game, "longest"),
        "games24h": games24h,
        "nightRate": night / n,
        "lossStreak": loss_streak,
        "winStreak": win_streak,
        "recent5Loss": len(recent5) >= 5 and all(not g.get("win") for g in recent5),
        "recent4Win": len(recent4) >= 4 and all(g.get("win") for g in recent4),
        "recent10WinRate": (sum(1 for g in recent10 if g.get("win")) / len(recent10)) if recent10 else None,
        "latestAgeDays": (now - max(created) / 1000.0) / 86400.0 if created else None,
        "spanDays": ((max(created) - min(created)) / 1000.0 / 86400.0) if len(created) > 1 else 0.0,
        "uniqueChampRate": len(champ_counts) / n,
        "topChampShare": top_count / n,
        "topChampWinRate": (top_wins / top_count) if top_count else None,
        "tankRate": class_rate("isTank"),
        "mageRate": class_rate("isMage"),
        "marksmanRate": class_rate("isMarksman"),
        "assassinRate": class_rate("isAssassin"),
        "supportRate": class_rate("isSupport"),
        "fighterRate": class_rate("isFighter"),
        "damageClassRate": class_rate("isDamageClass"),
        "kheadClassRate": class_rate("isKHeadClass"),
        "kheadClassGames": sum(1 for g in per_game if g.get("isKHeadClass")),
        "collectorRateInClass": (
            sum(1 for g in per_game if g.get("isKHeadClass") and g.get("collector"))
            / max(1, sum(1 for g in per_game if g.get("isKHeadClass")))
        ),
        "mathWizardGames": sum(1 for g in per_game if g.get("mathWizard")),
        "mathWizardRate": sum(1 for g in per_game if g.get("mathWizard")) / n,
        "critChanceMedian": _median_of(per_game, "critChance"),
        "heartsteelRate": _rate_of(per_game, "heartsteel"),
        "tankItemRate": sum(1 for g in per_game if (g.get("tankItems") or 0) >= 3) / n,
        "supportItemRate": sum(1 for g in per_game if (g.get("supportItems") or 0) >= 2) / n,
        "damageItemRate": sum(1 for g in per_game if (g.get("damageItems") or 0) >= 3) / n,
        "steelWarmogRate": _rate_of(per_game, "steelWarmog"),
        "offBuildRate": _rate_of(per_game, "offBuild"),
        "markRate": _rate_of(per_game, "mark"),
        "avgAugmentPerf": _mean_of(per_game, "augPerf"),
        "offMetaRate": (sum(1 for g in per_game if g.get("offMeta")) / n) if total_champs else None,
        "full_team": (team_games / n) >= 0.8,
        "full_team_games": team_games,
        "champion_counts": champ_counts,
        "per_game": per_game,
    }
    return metrics


# ---------------------------------------------------------------------------
# condition tree


def _eval(condition, metrics):
    if not condition:
        return True
    if "all" in condition:
        return all(_eval(c, metrics) for c in condition["all"])
    if "any" in condition:
        return any(_eval(c, metrics) for c in condition["any"])
    if "not" in condition:
        return not _eval(condition["not"], metrics)
    metric = condition.get("metric")
    value = metrics.get(metric)
    if value is None:
        return False
    target = condition.get("value")
    op = condition.get("op")
    try:
        if op == ">=":
            return value >= target
        if op == "<=":
            return value <= target
        if op == ">":
            return value > target
        if op == "<":
            return value < target
        if op == "==":
            return value == target
        if op == "!=":
            return value != target
    except TypeError:
        return False
    return False


def _hits(rule, per_game):
    condition = rule.get("per_game")
    if condition == "same":
        condition = rule.get("when")
    if not condition:
        return None
    return sum(1 for metrics in per_game if _eval(condition, metrics))


# ---------------------------------------------------------------------------
# rule table


def _rule(**kwargs):
    defaults = {
        "family": "other",
        "tone": "neutral",
        "kind": "behavior",
        "priority": 50,
        "min_games": 5,
        "min_hit_rate": 0.4,
        "enabled": True,
        "excludes": [],
        "per_game": "same",
        "when": None,
        "evidence": "",
    }
    defaults.update(kwargs)
    return defaults


RULES = [
    # ----- output ---------------------------------------------------------
    _rule(
        id="carry",
        family="damage",
        tone="positive",
        priority=86,
        min_games=8,
        min_hit_rate=0.5,
        name={"roast": "大爹", "neutral": "输出核心"},
        when={"all": [
            {"metric": "dmgShare", "op": ">=", "value": 0.30},
            {"metric": "winRate", "op": ">=", "value": 0.50},
        ]},
        # A low-damage player should never be called 大爹: require a high share
        # in the high-damage games as well as a winning sample.
        per_game={"all": [
            {"metric": "dmgShare", "op": ">=", "value": 0.28},
            {"metric": "win", "op": "==", "value": True},
        ]},
        excludes=["invisible", "scratch", "feeder"],
        evidence="伤害占比 {dmgShare:.0%} · 胜率 {winRate:.0%} · {hits}/{n} 场",
    ),
    _rule(
        id="silent_carry",
        family="damage",
        tone="positive",
        priority=72,
        name={"roast": "隐形大爹", "neutral": "无声输出"},
        when={"all": [
            {"metric": "dmgShare", "op": ">=", "value": 0.24},
            {"metric": "killShare", "op": "<=", "value": 0.20},
            {"metric": "assistShare", "op": ">=", "value": 0.20},
        ]},
        per_game={"all": [
            {"metric": "dmgShare", "op": ">=", "value": 0.24},
            {"metric": "killShare", "op": "<=", "value": 0.20},
            {"metric": "assistShare", "op": ">=", "value": 0.20},
        ]},
        excludes=["kill_hog", "carry"],
        evidence="伤害占比 {dmgShare:.0%} · 击杀占比 {killShare:.0%} · 助攻占比 {assistShare:.0%}",
    ),
    _rule(
        id="glass_cannon",
        family="damage",
        tone="neutral",
        priority=60,
        name={"roast": "玻璃大炮", "neutral": "玻璃大炮"},
        when={"all": [
            {"metric": "dmgShare", "op": ">=", "value": 0.24},
            {"any": [
                {"metric": "deathShare", "op": ">=", "value": 0.26},
                {"metric": "deathsPerMin", "op": ">=", "value": 0.55},
            ]},
        ]},
        per_game={"all": [
            {"metric": "dmgShare", "op": ">=", "value": 0.24},
            {"any": [
                {"metric": "deathShare", "op": ">=", "value": 0.26},
                {"metric": "deathsPerMin", "op": ">=", "value": 0.55},
            ]},
        ]},
        evidence="伤害占比 {dmgShare:.0%} · 死亡占比 {deathShare:.0%}",
    ),
    _rule(
        id="scratch",
        family="damage",
        tone="roast",
        priority=64,
        min_games=8,
        min_hit_rate=0.5,
        name={"roast": "刮痧师傅", "neutral": "低转化输出"},
        when={"all": [
            {"metric": "damageClassRate", "op": ">=", "value": 0.55},
            {"metric": "dmgShare", "op": "<=", "value": 0.16},
            {"metric": "goldShare", "op": ">=", "value": 0.15},
        ]},
        per_game={"all": [
            {"metric": "isDamageClass", "op": "==", "value": True},
            {"metric": "dmgShare", "op": "<=", "value": 0.16},
            {"metric": "goldShare", "op": ">=", "value": 0.15},
        ]},
        excludes=["carry", "silent_carry"],
        evidence="伤害占比 {dmgShare:.0%} · 金币占比 {goldShare:.0%} · {hits}/{n} 场",
    ),
    # ----- kills ----------------------------------------------------------
    _rule(
        id="kill_hog",
        family="kills",
        tone="roast",
        priority=88,
        min_games=8,
        min_hit_rate=0.6,
        name={"roast": "人头狗", "neutral": "收头位"},
        when={"all": [
            {"metric": "killsPerMin", "op": ">=", "value": 0.45},
            {"any": [
                {"metric": "killShareMinusDamageShare", "op": ">=", "value": 0.10},
                {"metric": "dmgPerKill", "op": "<=", "value": 3200},
            ]},
            {"metric": "assistShare", "op": "<=", "value": 0.20},
        ]},
        per_game={"all": [
            {"metric": "killsPerMin", "op": ">=", "value": 0.45},
            {"any": [
                {"metric": "killShareMinusDamageShare", "op": ">=", "value": 0.10},
                {"metric": "dmgPerKill", "op": "<=", "value": 3200},
            ]},
            {"metric": "assistShare", "op": "<=", "value": 0.20},
        ]},
        excludes=["generous", "carry"],
        evidence="击杀占比 {killShare:.0%} · 伤害占比 {dmgShare:.0%} · 伤害/击杀 {dmgPerKill:.0f} · {hits}/{n} 场",
    ),
    _rule(
        id="generous",
        family="kills",
        tone="positive",
        priority=62,
        name={"roast": "让头菩萨", "neutral": "让头位"},
        when={"all": [
            {"metric": "assistShare", "op": ">=", "value": 0.30},
            {"metric": "killShare", "op": "<=", "value": 0.15},
            {"metric": "dmgShare", "op": ">=", "value": 0.18},
        ]},
        per_game={"all": [
            {"metric": "assistShare", "op": ">=", "value": 0.30},
            {"metric": "killShare", "op": "<=", "value": 0.15},
            {"metric": "dmgShare", "op": ">=", "value": 0.18},
        ]},
        excludes=["kill_hog"],
        evidence="助攻占比 {assistShare:.0%} · 击杀占比 {killShare:.0%}",
    ),
    _rule(
        id="no_kill",
        family="kills",
        tone="roast",
        priority=48,
        min_games=8,
        min_hit_rate=0.5,
        name={"roast": "空手道", "neutral": "零击杀"},
        when={"all": [
            {"metric": "killsPerMin", "op": "<=", "value": 0.15},
            {"metric": "deathsPerMin", "op": ">=", "value": 0.30},
        ]},
        per_game={"all": [
            {"metric": "killsPerMin", "op": "<=", "value": 0.15},
            {"metric": "deathsPerMin", "op": ">=", "value": 0.30},
        ]},
        evidence="击杀/分 {killsPerMin:.2f} · 死亡/分 {deathsPerMin:.2f} · {hits}/{n} 场",
    ),
    # ----- tank -----------------------------------------------------------
    _rule(
        id="wall",
        family="tank",
        tone="positive",
        priority=90,
        name={"roast": "城墙", "neutral": "城墙"},
        when={"all": [
            {"any": [
                {"metric": "tankRate", "op": ">=", "value": 0.35},
                {"metric": "tankItemRate", "op": ">=", "value": 0.45},
            ]},
            {"metric": "takenShare", "op": ">=", "value": 0.24},
            {"metric": "deathsPerMin", "op": "<=", "value": 0.55},
        ]},
        per_game={"all": [
            {"any": [
                {"metric": "isTank", "op": "==", "value": True},
                {"metric": "tankItems", "op": ">=", "value": 3},
            ]},
            {"metric": "takenShare", "op": ">=", "value": 0.24},
            {"metric": "deathsPerMin", "op": "<=", "value": 0.55},
        ]},
        excludes=["paper_front", "backline_tank"],
        evidence="承伤占比 {takenShare:.0%} · 心之钢 {heartsteelRate:.0%} · 死亡/分 {deathsPerMin:.2f} · {hits}/{n} 场",
    ),
    _rule(
        id="heartsteel",
        family="tank",
        tone="neutral",
        kind="stable",
        min_games=5,
        priority=70,
        name={"roast": "心之钢信徒", "neutral": "心之钢信徒"},
        when={"all": [
            {"metric": "heartsteelRate", "op": ">=", "value": 0.50},
            {"metric": "takenPerMin", "op": ">=", "value": 1500},
        ]},
        evidence="心之钢 {heartsteelRate:.0%} 场 · 承伤/分 {takenPerMin:.0f}",
    ),
    _rule(
        id="iron_turtle",
        family="tank",
        tone="roast",
        priority=58,
        min_games=8,
        min_hit_rate=0.5,
        name={"roast": "铁王八", "neutral": "纯肉盾"},
        when={"all": [
            {"metric": "takenShare", "op": ">=", "value": 0.28},
            {"metric": "dmgShare", "op": "<=", "value": 0.15},
            {"metric": "deathsPerMin", "op": "<=", "value": 0.40},
        ]},
        per_game={"all": [
            {"metric": "takenShare", "op": ">=", "value": 0.28},
            {"metric": "dmgShare", "op": "<=", "value": 0.15},
            {"metric": "deathsPerMin", "op": "<=", "value": 0.40},
        ]},
        excludes=["wall"],
        evidence="承伤占比 {takenShare:.0%} · 伤害占比 {dmgShare:.0%}",
    ),
    _rule(
        id="sacrifice",
        family="tank",
        tone="neutral",
        priority=55,
        name={"roast": "敢死队", "neutral": "敢死队"},
        when={"all": [
            {"metric": "takenShare", "op": ">=", "value": 0.22},
            {"metric": "deathShare", "op": ">=", "value": 0.24},
        ]},
        per_game={"all": [
            {"metric": "takenShare", "op": ">=", "value": 0.22},
            {"metric": "deathShare", "op": ">=", "value": 0.24},
        ]},
        excludes=["feeder"],
        evidence="承伤占比 {takenShare:.0%} · 死亡占比 {deathShare:.0%}",
    ),
    _rule(
        id="engage",
        family="tank",
        tone="positive",
        priority=68,
        name={"roast": "开团机器", "neutral": "开团手"},
        when={"all": [
            {"metric": "ccShare", "op": ">=", "value": 0.25},
            {"metric": "takenShare", "op": ">=", "value": 0.20},
            {"metric": "deathsPerMin", "op": ">=", "value": 0.40},
        ]},
        per_game={"all": [
            {"metric": "ccShare", "op": ">=", "value": 0.25},
            {"metric": "takenShare", "op": ">=", "value": 0.20},
            {"metric": "deathsPerMin", "op": ">=", "value": 0.40},
        ]},
        evidence="控制占比 {ccShare:.0%} · 承伤占比 {takenShare:.0%}",
    ),
    _rule(
        id="paper_front",
        family="tank",
        tone="roast",
        priority=74,
        min_games=8,
        min_hit_rate=0.5,
        name={"roast": "纸糊前排", "neutral": "脆皮前排"},
        when={"all": [
            {"metric": "tankRate", "op": ">=", "value": 0.45},
            {"metric": "takenShare", "op": "<=", "value": 0.20},
            {"metric": "deathsPerMin", "op": ">=", "value": 0.45},
        ]},
        per_game={"all": [
            {"metric": "isTank", "op": "==", "value": True},
            {"metric": "takenShare", "op": "<=", "value": 0.20},
            {"metric": "deathsPerMin", "op": ">=", "value": 0.45},
        ]},
        excludes=["wall"],
        evidence="承伤占比 {takenShare:.0%} · 死亡/分 {deathsPerMin:.2f} · {hits}/{n} 场",
    ),
    _rule(
        id="backline_tank",
        family="tank",
        tone="roast",
        priority=73,
        min_games=8,
        min_hit_rate=0.5,
        name={"roast": "后排坦克", "neutral": "输出型坦克"},
        when={"all": [
            {"metric": "tankRate", "op": ">=", "value": 0.45},
            {"metric": "dmgShare", "op": ">=", "value": 0.22},
            {"metric": "takenShare", "op": "<=", "value": 0.20},
        ]},
        per_game={"all": [
            {"metric": "isTank", "op": "==", "value": True},
            {"metric": "dmgShare", "op": ">=", "value": 0.22},
            {"metric": "takenShare", "op": "<=", "value": 0.20},
        ]},
        excludes=["wall"],
        evidence="伤害占比 {dmgShare:.0%} · 承伤占比 {takenShare:.0%} · {hits}/{n} 场",
    ),
    # ----- support --------------------------------------------------------
    _rule(
        id="heal_fountain",
        family="support",
        tone="positive",
        priority=78,
        name={"roast": "移动泉水", "neutral": "治疗核心"},
        when={"all": [
            {"metric": "healShare", "op": ">=", "value": 0.28},
            {"metric": "healPerMin", "op": ">=", "value": 600},
        ]},
        per_game={"all": [
            {"metric": "healShare", "op": ">=", "value": 0.28},
            {"metric": "healPerMin", "op": ">=", "value": 600},
        ]},
        evidence="治疗占比 {healShare:.0%} · 治疗/分 {healPerMin:.0f}",
    ),
    _rule(
        id="nanny",
        family="support",
        tone="positive",
        priority=64,
        name={"roast": "保姆", "neutral": "团队辅助"},
        when={"all": [
            {"metric": "assistShare", "op": ">=", "value": 0.28},
            {"metric": "goldShare", "op": "<=", "value": 0.18},
            {"metric": "supportShare", "op": ">=", "value": 0.30},
        ]},
        per_game={"all": [
            {"metric": "assistShare", "op": ">=", "value": 0.28},
            {"metric": "goldShare", "op": "<=", "value": 0.18},
            {"metric": "supportShare", "op": ">=", "value": 0.30},
        ]},
        evidence="助攻占比 {assistShare:.0%} · 金币占比 {goldShare:.0%}",
    ),
    _rule(
        id="cc_master",
        family="support",
        tone="positive",
        priority=60,
        name={"roast": "罚站王", "neutral": "控制核心"},
        when={"all": [{"metric": "ccShare", "op": ">=", "value": 0.30}]},
        per_game={"all": [{"metric": "ccShare", "op": ">=", "value": 0.30}]},
        evidence="控制占比 {ccShare:.0%}",
    ),
    # ----- death / presence ----------------------------------------------
    _rule(
        id="feeder",
        family="death",
        tone="roast",
        priority=84,
        min_games=8,
        min_hit_rate=0.5,
        name={"roast": "提款机", "neutral": "高死亡"},
        when={"all": [
            {"metric": "deathShare", "op": ">=", "value": 0.26},
            {"metric": "kp", "op": "<=", "value": 0.45},
        ]},
        per_game={"all": [
            {"metric": "deathShare", "op": ">=", "value": 0.26},
            {"metric": "kp", "op": "<=", "value": 0.45},
        ]},
        excludes=["sacrifice", "kda_protector"],
        evidence="死亡占比 {deathShare:.0%} · 参团 {kp:.0%} · {hits}/{n} 场",
    ),
    _rule(
        id="death_or_glory",
        family="death",
        tone="neutral",
        priority=57,
        name={"roast": "敢死队长", "neutral": "敢死队长"},
        when={"all": [
            {"metric": "deathsPerMin", "op": ">=", "value": 0.65},
            {"metric": "dmgShare", "op": ">=", "value": 0.22},
        ]},
        per_game={"all": [
            {"metric": "deathsPerMin", "op": ">=", "value": 0.65},
            {"metric": "dmgShare", "op": ">=", "value": 0.22},
        ]},
        excludes=["feeder"],
        evidence="死亡/分 {deathsPerMin:.2f} · 伤害占比 {dmgShare:.0%}",
    ),
    _rule(
        id="invisible",
        family="death",
        tone="roast",
        priority=82,
        min_games=8,
        min_hit_rate=0.5,
        name={"roast": "隐身哥", "neutral": "低存在感"},
        when={"all": [
            {"metric": "dmgShare", "op": "<=", "value": 0.13},
            {"metric": "takenShare", "op": "<=", "value": 0.15},
            {"metric": "kp", "op": "<=", "value": 0.35},
        ]},
        per_game={"all": [
            {"metric": "dmgShare", "op": "<=", "value": 0.13},
            {"metric": "takenShare", "op": "<=", "value": 0.15},
            {"metric": "kp", "op": "<=", "value": 0.35},
        ]},
        excludes=["carry", "silent_carry"],
        evidence="伤害 {dmgShare:.0%} · 承伤 {takenShare:.0%} · 参团 {kp:.0%} · {hits}/{n} 场",
    ),
    _rule(
        id="afk_suspect",
        family="death",
        tone="roast",
        kind="extreme",
        priority=92,
        min_games=3,
        min_hit_rate=0.66,
        name={"roast": "挂机嫌疑", "neutral": "挂机嫌疑"},
        when={"all": [
            {"metric": "dmgShare", "op": "<=", "value": 0.04},
            {"metric": "goldPerMin", "op": "<=", "value": 180},
        ]},
        per_game={"all": [
            {"metric": "dmgShare", "op": "<=", "value": 0.04},
            {"metric": "goldPerMin", "op": "<=", "value": 180},
        ]},
        evidence="伤害占比 {dmgShare:.0%} · 金币/分 {goldPerMin:.0f} · {hits}/{n} 场（低置信）",
    ),
    _rule(
        id="kda_protector",
        family="death",
        tone="roast",
        priority=52,
        min_games=8,
        min_hit_rate=0.5,
        name={"roast": "保KDA", "neutral": "保守站位"},
        when={"all": [
            {"metric": "deathsPerMin", "op": "<=", "value": 0.25},
            {"metric": "kp", "op": "<=", "value": 0.40},
            {"metric": "dmgShare", "op": "<=", "value": 0.17},
        ]},
        per_game={"all": [
            {"metric": "deathsPerMin", "op": "<=", "value": 0.25},
            {"metric": "kp", "op": "<=", "value": 0.40},
            {"metric": "dmgShare", "op": "<=", "value": 0.17},
        ]},
        evidence="死亡/分 {deathsPerMin:.2f} · 参团 {kp:.0%} · 伤害 {dmgShare:.0%}",
    ),
    _rule(
        id="respawn_racer",
        family="death",
        tone="roast",
        priority=50,
        min_games=8,
        min_hit_rate=0.5,
        name={"roast": "落地成盒", "neutral": "存活偏短"},
        when={"all": [{"metric": "longestMedian", "op": "<=", "value": 55}]},
        per_game={"all": [{"metric": "longest", "op": "<=", "value": 55}]},
        evidence="最长存活中位数 {longestMedian:.0f}s · {hits}/{n} 场",
    ),
    # ----- economy / build ------------------------------------------------
    _rule(
        id="gold_hoarder",
        family="economy",
        tone="roast",
        priority=56,
        min_games=8,
        min_hit_rate=0.5,
        name={"roast": "财迷", "neutral": "经济高转化低"},
        when={"all": [
            {"metric": "goldShare", "op": ">=", "value": 0.20},
            {"metric": "dmgShare", "op": "<=", "value": 0.17},
        ]},
        per_game={"all": [
            {"metric": "goldShare", "op": ">=", "value": 0.20},
            {"metric": "dmgShare", "op": "<=", "value": 0.17},
        ]},
        evidence="金币占比 {goldShare:.0%} · 伤害占比 {dmgShare:.0%}",
    ),
    _rule(
        id="k_head",
        family="economy",
        tone="roast",
        kind="stable",
        min_games=5,
        priority=64,
        name={"roast": "K头者", "neutral": "K头者"},
        when={"all": [
            {"metric": "kheadClassRate", "op": ">=", "value": 0.60},
            {"metric": "collectorRateInClass", "op": ">=", "value": 0.80},
        ]},
        evidence="射/战/刺 {kheadClassRate:.0%} 场 · 其中收集者 {collectorRateInClass:.0%}",
    ),
    _rule(
        id="math_wizard",
        family="economy",
        tone="positive",
        kind="stable",
        min_games=5,
        priority=62,
        name={"roast": "数学奇才", "neutral": "数学奇才"},
        when={"all": [{"metric": "mathWizardGames", "op": ">=", "value": 1}]},
        evidence="暴击强化 + 125% 暴击 {mathWizardGames}/{n} 场",
    ),
    _rule(
        id="mad_scientist",
        family="economy",
        tone="neutral",
        kind="stable",
        min_games=5,
        priority=58,
        name={"roast": "科研家", "neutral": "出装鬼才"},
        when={"all": [{"metric": "offBuildRate", "op": ">=", "value": 0.40}]},
        evidence="非主流出装 {offBuildRate:.0%} 场",
    ),
    _rule(
        id="steel_warmog",
        family="economy",
        tone="neutral",
        kind="stable",
        min_games=5,
        priority=56,
        name={"roast": "钢门", "neutral": "钢门"},
        when={"all": [{"metric": "steelWarmogRate", "op": ">=", "value": 0.30}]},
        evidence="心之钢+狂徒 {steelWarmogRate:.0%} 场",
    ),
    # ----- champion pool --------------------------------------------------
    _rule(
        id="one_trick",
        family="pool",
        tone="positive",
        kind="stable",
        min_games=5,
        priority=62,
        name={"roast": "绝活哥", "neutral": "本命玩家"},
        when={"all": [
            {"metric": "topChampShare", "op": ">=", "value": 0.35},
            {"metric": "topChampWinRate", "op": ">=", "value": 0.50},
        ]},
        evidence="单一英雄 {topChampShare:.0%} 场 · 该英雄胜率 {topChampWinRate:.0%}",
    ),
    _rule(
        id="wide_pool",
        family="pool",
        tone="neutral",
        kind="stable",
        min_games=10,
        priority=40,
        name={"roast": "英雄海", "neutral": "英雄海"},
        when={"all": [{"metric": "uniqueChampRate", "op": ">=", "value": 0.80}]},
        evidence="独特英雄 {uniqueChampRate:.0%}",
    ),
    # ----- mentality / session -------------------------------------------
    _rule(
        id="lose_streak",
        family="session",
        tone="roast",
        kind="session",
        min_games=5,
        priority=46,
        name={"roast": "连败战神", "neutral": "连败中"},
        when={"all": [
            {"metric": "recent5Loss", "op": "==", "value": True},
            {"metric": "games24h", "op": ">=", "value": 4},
        ]},
        evidence="最近 5 场全负 · 24h {games24h} 场",
    ),
    _rule(
        id="win_streak",
        family="session",
        tone="positive",
        kind="session",
        min_games=4,
        priority=44,
        name={"roast": "连胜王", "neutral": "连胜中"},
        when={"all": [{"metric": "winStreak", "op": ">=", "value": 3}]},
        evidence="当前 {winStreak} 连胜",
    ),
    _rule(
        id="night_owl",
        family="session",
        tone="neutral",
        kind="session",
        min_games=5,
        priority=38,
        name={"roast": "深夜战士", "neutral": "深夜出没"},
        when={"all": [{"metric": "nightRate", "op": ">=", "value": 0.40}]},
        evidence="0–5 点场次 {nightRate:.0%}",
    ),
    _rule(
        id="grinder",
        family="session",
        tone="neutral",
        kind="session",
        min_games=0,
        priority=36,
        name={"roast": "肝帝", "neutral": "高强度玩家"},
        when={"all": [{"metric": "games24h", "op": ">=", "value": 8}]},
        evidence="24h 内 {games24h} 场",
    ),
    _rule(
        id="low_form",
        family="session",
        tone="neutral",
        kind="session",
        min_games=5,
        priority=34,
        name={"roast": "状态低迷", "neutral": "状态低迷"},
        when={"all": [
            {"metric": "recent10WinRate", "op": "<=", "value": 0.35},
            {"metric": "dmgShare", "op": "<=", "value": 0.18},
            {"metric": "deathsPerMin", "op": ">=", "value": 0.55},
        ]},
        evidence="近 10 场胜率 {recent10WinRate:.0%} · 伤害 {dmgShare:.0%}",
    ),
    # ----- mayhem ---------------------------------------------------------
    _rule(
        id="chosen",
        family="mayhem",
        tone="positive",
        kind="augment",
        min_games=5,
        priority=64,
        name={"roast": "天选之人", "neutral": "强化运气好"},
        when={"all": [{"metric": "avgAugmentPerf", "op": ">=", "value": 82}]},
        evidence="强化平均分 {avgAugmentPerf:.1f}（约前 25%）",
    ),
    _rule(
        id="gambler",
        family="mayhem",
        tone="roast",
        kind="augment",
        min_games=8,
        priority=42,
        name={"roast": "赌狗", "neutral": "强化运气差"},
        when={"all": [{"metric": "avgAugmentPerf", "op": "<=", "value": 78}]},
        evidence="强化平均分 {avgAugmentPerf:.1f}（约后 25%）",
    ),
    _rule(
        id="off_meta",
        family="mayhem",
        tone="neutral",
        kind="stable",
        min_games=8,
        priority=40,
        name={"roast": "逆版本勇士", "neutral": "逆版本"},
        when={"all": [{"metric": "offMetaRate", "op": ">=", "value": 0.40}]},
        evidence="后 30% 英雄 {offMetaRate:.0%} 场",
    ),
    _rule(
        id="snowball",
        family="mayhem",
        tone="neutral",
        kind="stable",
        min_games=8,
        priority=35,
        name={"roast": "雪球信徒", "neutral": "雪球信徒"},
        when={"all": [{"metric": "markRate", "op": ">=", "value": 0.60}]},
        evidence="带雪球 {markRate:.0%} 场",
    ),
]

RULE_BY_ID = {rule["id"]: rule for rule in RULES}


# ---------------------------------------------------------------------------
# data-quality tags


def _data_tag(tag_id, name, evidence, tone="data"):
    return {
        "id": tag_id,
        "family": "data",
        "name": name,
        "tone": tone,
        "kind": "data",
        "priority": 100,
        "confidence": "—",
        "evidence": evidence,
        "hits": None,
        "n": None,
    }


def reason_tag(reason, metrics=None, context=None):
    metrics = metrics or {}
    context = context or {}
    n = metrics.get("n") or 0
    level = context.get("level")
    if reason == "anonymous":
        return _data_tag("anonymous", "神秘人 · 匿名者", "选人阶段没有真实 puuid")
    if reason == "private":
        return _data_tag("private", "神秘人 · 隐藏生涯", "生涯隐藏，SGP 未返回战绩")
    if reason == "unavailable":
        return _data_tag("unavailable", "数据暂不可用", "SGP/LCU 超时或被拒绝，可重试")
    if reason == "no_aram":
        return _data_tag("no_aram", "非大乱斗玩家", "最近 30 场里 ARAM 少于 3 场")
    if reason == "low_sample":
        return _data_tag("low_sample", "样本不足", "仅 %d 场 ARAM" % n)
    if reason == "new_account":
        suffix = "，等级 %s" % level if level else ""
        return _data_tag("new_account", "新号/小号", "ARAM 样本少%s" % suffix)
    return None


def _confidence(metrics):
    n = metrics.get("n") or 0
    full = bool(metrics.get("full_team"))
    if n >= 12 and full:
        return "高"
    if n >= 8:
        return "中"
    return "低"


def _format_evidence(template, metrics, hits=None, n=None):
    if not template:
        return ""
    pattern = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::([^}]+))?\}")

    def repl(match):
        key = match.group(1)
        spec = match.group(2)
        if key == "hits":
            value = hits
        elif key == "n":
            value = n if n is not None else metrics.get("n")
        else:
            value = metrics.get(key)
        if value is None:
            return "—"
        if spec:
            try:
                return format(value, spec)
            except (ValueError, TypeError):
                return str(value)
        return str(value)

    return pattern.sub(repl, template)


def apply_exclusions(tags):
    """Drop lower-priority tags that declare each other incompatible."""
    selected = []
    for tag in sorted(tags, key=lambda t: t.get("priority") or 0, reverse=True):
        if tag.get("kind") == "data":
            selected.append(tag)
            continue
        blocked = False
        for other in selected:
            if other.get("kind") == "data":
                continue
            if tag["id"] in (other.get("excludes") or []) or other["id"] in (tag.get("excludes") or []):
                blocked = True
                break
        if not blocked:
            selected.append(tag)
    return selected


_OVERRIDE_CACHE = {"path": None, "mtime": None, "rules": None}


def _deep_merge(base, patch):
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_rules(data_dir=None):
    """Return RULES with optional local overrides merged in.

    ``data/tag-overrides.json`` is written by scripts/tag-admin.py. A missing or
    malformed file always falls back to the built-in defaults.
    """
    if not data_dir:
        return [copy.deepcopy(rule) for rule in RULES]
    path = os.path.join(data_dir, "tag-overrides.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    if (_OVERRIDE_CACHE["path"] == path and _OVERRIDE_CACHE["mtime"] == mtime
            and _OVERRIDE_CACHE["rules"] is not None):
        return _OVERRIDE_CACHE["rules"]

    rules = [copy.deepcopy(rule) for rule in RULES]
    if mtime:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            patches = data.get("rules") if isinstance(data, dict) else None
            if isinstance(patches, dict):
                by_id = {rule["id"]: rule for rule in rules}
                for rule_id, patch in patches.items():
                    if rule_id in by_id and isinstance(patch, dict):
                        _deep_merge(by_id[rule_id], patch)
        except Exception:
            rules = [copy.deepcopy(rule) for rule in RULES]
    _OVERRIDE_CACHE.update(path=path, mtime=mtime, rules=rules)
    return rules


def _candidate(rule, metrics, n):
    if rule.get("enabled") is False:
        return None
    kind = rule.get("kind") or "behavior"
    tone = rule.get("tone") or "neutral"
    min_games = int(rule.get("min_games") or 0)
    if n < min_games:
        return None
    if n < 5 and kind not in ("stable", "extreme", "session"):
        return None
    if n < 8 and tone == "roast" and kind in ("behavior", "augment"):
        return None
    if not _eval(rule.get("when"), metrics):
        return None
    hits = None
    if kind in ("behavior", "extreme"):
        hits = _hits(rule, metrics.get("per_game") or [])
        if hits is None:
            return None
        rate = hits / n if n else 0.0
        if rate < float(rule.get("min_hit_rate") or 0.0):
            return None
    return {
        "id": rule["id"],
        "family": rule.get("family"),
        "name": (rule.get("name") or {}).get("roast") or rule["id"],
        "tone": tone,
        "kind": kind,
        "priority": rule.get("priority") or 0,
        "confidence": _confidence(metrics),
        "evidence": _format_evidence(rule.get("evidence"), metrics, hits, n),
        "hits": hits,
        "n": n,
    }


def evaluate(metrics, context=None, rules=None):
    """Return play-style tags (max 3) plus at most one data-quality tag."""
    context = context or {}
    rules = rules if rules is not None else RULES
    n = int(metrics.get("n") or 0)
    reason = context.get("reason")

    early = reason_tag(reason, metrics, context)
    if early is not None:
        return [early]
    if n < 5:
        # AFK is the one rule allowed below the normal five-game floor: it is
        # evidence-based ("2 of 3 games at 3% damage") and marked low-confidence.
        extremes = []
        for rule in rules:
            if (rule.get("kind") or "") != "extreme":
                continue
            tag = _candidate(rule, metrics, n)
            if tag:
                extremes.append(tag)
        return extremes[:1] + [reason_tag("low_sample", metrics, context)]

    candidates = []
    for rule in rules:
        tag = _candidate(rule, metrics, n)
        if tag:
            candidates.append(tag)

    selected = apply_exclusions(candidates)
    play = [t for t in selected if t.get("kind") != "data"][:3]

    data = None
    level = context.get("level")
    if context.get("new_account"):
        data = reason_tag("new_account", metrics, context)
    elif (
        n >= 5
        and level
        and level <= 100
        and metrics.get("winRate") is not None
        and metrics["winRate"] >= 0.65
        and (metrics.get("kda") or 0) >= 4
    ):
        data = _data_tag(
            "smurf",
            "炸鱼",
            "胜率 {:.0%} · KDA {:.1f} · {} 场".format(metrics["winRate"], metrics.get("kda") or 0, n),
        )
    elif n < 8:
        data = reason_tag("low_sample", metrics, context)

    return play + ([data] if data else [])
