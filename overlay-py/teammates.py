"""Fetch recent ARAM: Mayhem games for the five champion-select allies and
turn them into play-style tags.

Data sources, in order:
  1. LCU match list (one participant per game) -> LCU game detail (10 players)
  2. SGP SUMMARY -> SGP DETAILS (10 players, flat stats)
  3. LCU list only -> rate-based fallback, low confidence

The module is intentionally defensive: the client is a moving target, and one
player with hidden history must never break the other four rows.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import lcu
import sgp
import tagrules

if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(ROOT, "data")
# Source runs write inside the repo; the packaged build may live in a read-only
# folder, so it uses %LOCALAPPDATA%\LeagueSoraka (same place as the client path).
if getattr(sys, "frozen", False):
    _CACHE_DIR = getattr(lcu, "CACHE_DIR", None) or os.path.join(ROOT, ".cache")
else:
    _CACHE_DIR = os.path.join(ROOT, ".cache")
CACHE_PATH = os.path.join(_CACHE_DIR, "teammates.json")
CACHE_VERSION = 1
PROFILE_TTL = 30 * 60
GAME_TTL = 24 * 60 * 60
ARAM_QUEUES = (2400, 450)
ARAM_MAP = 12
ZERO_PUUID = "00000000-0000-0000-0000-000000000000"

_LOCK = threading.Lock()
_CACHE = None
_LOOKUPS = None


# ---------------------------------------------------------------------------
# small json / cache helpers


def _load_json(path, fallback=None):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return fallback


def _load_cache():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    raw = _load_json(CACHE_PATH, None)
    if not isinstance(raw, dict) or raw.get("version") != CACHE_VERSION:
        raw = {"version": CACHE_VERSION, "games": {}, "profiles": {}}
    raw.setdefault("games", {})
    raw.setdefault("profiles", {})
    _CACHE = raw
    return _CACHE


def _save_cache():
    cache = _load_cache()
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)
    except Exception:
        pass


def clear_cache():
    global _CACHE
    with _LOCK:
        _CACHE = {"version": CACHE_VERSION, "games": {}, "profiles": {}}
    try:
        os.remove(CACHE_PATH)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# lookups (champion tags / ranks / augment performance)


def lookups():
    global _LOOKUPS
    if _LOOKUPS is not None:
        return _LOOKUPS
    champions = (_load_json(os.path.join(DATA_DIR, "champions.json"), {}) or {}).get("champions") or []
    stats = (_load_json(os.path.join(DATA_DIR, "stats.json"), {}) or {}).get("byChampion") or {}
    catalog = _load_json(os.path.join(DATA_DIR, "catalog.json"), {}) or {}
    item_data = _load_json(os.path.join(DATA_DIR, "items.json"), {}) or {}
    tags = {}
    ranks = {}
    keys = {}
    names = {}
    for champ in champions:
        try:
            cid = int(champ.get("id"))
        except (TypeError, ValueError):
            continue
        tags[cid] = list(champ.get("tags") or [])
        ranks[cid] = champ.get("rank")
        keys[cid] = champ.get("key")
        names[cid] = champ.get("nameZh") or champ.get("nameEn") or str(cid)

    item_crit = {}
    for item_id, row in (item_data.get("items") or {}).items():
        try:
            item_crit[int(item_id)] = float((row or {}).get("crit") or 0)
        except (TypeError, ValueError):
            continue

    augment_crit = {}
    for augment in catalog.get("augments") or []:
        try:
            augment_id = int((augment or {}).get("id"))
        except (TypeError, ValueError):
            continue
        text = " ".join(str(augment.get(key) or "") for key in ("descZh", "desc"))
        match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*暴击率", text)
        if not match:
            match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:critical strike chance|crit chance)", text, re.I)
        if match:
            bonus = float(match.group(1))
        elif "暴击率" in text or "critical strike chance" in text.lower():
            bonus = 25.0  # descriptions with "?" still grant the standard 25%
        else:
            bonus = 0.0
        if bonus:
            augment_crit[augment_id] = bonus
    for augment_id, bonus in getattr(tagrules, "CRIT_AUGMENT_IDS", {}).items():
        augment_crit.setdefault(int(augment_id), float(bonus))

    _LOOKUPS = {
        "champion_tags": tags,
        "champion_rank": ranks,
        "champion_key": keys,
        "champion_name": names,
        "champion_total": len(champions) or 173,
        "augment_scores": stats,
        "item_crit": item_crit,
        "augment_crit": augment_crit,
    }
    return _LOOKUPS


def champion_name(champion_id):
    return (lookups().get("champion_name") or {}).get(int(champion_id or 0)) or "?"


# ---------------------------------------------------------------------------
# normalization: LCU nested stats and SGP flat stats


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stat(entry, key, default=0):
    stats = entry.get("stats")
    if isinstance(stats, dict) and key in stats:
        return stats.get(key)
    return entry.get(key, default)


def _unwrap(raw):
    """SUMMARY items are {metadata, json}; return the full game inside."""
    if isinstance(raw, dict) and "metadata" in raw and "json" in raw:
        return sgp.unwrap(raw)
    return raw


def _participants(raw):
    raw = _unwrap(raw)
    if not isinstance(raw, dict):
        return []
    direct = raw.get("participants")
    if isinstance(direct, list) and direct:
        return direct
    nested = raw.get("json")
    if isinstance(nested, str):
        try:
            nested = json.loads(nested)
        except Exception:
            nested = None
    if isinstance(nested, dict):
        inner = nested.get("participants")
        if isinstance(inner, list):
            return inner
    return []


def _identities(raw):
    out = {}
    for identity in raw.get("participantIdentities") or []:
        if not isinstance(identity, dict):
            continue
        pid = identity.get("participantId")
        player = identity.get("player") or {}
        if pid is not None and player.get("puuid"):
            out[pid] = player.get("puuid")
    return out


def _normalize_participant(entry, identities):
    if not isinstance(entry, dict):
        return None
    pid = entry.get("participantId")
    puuid = entry.get("puuid") or identities.get(pid)
    champion_id = _int(entry.get("championId") or entry.get("champion_id"))
    if not champion_id:
        return None
    items = [_int(_stat(entry, "item%d" % index)) for index in range(7)]
    augments = [_int(_stat(entry, "playerAugment%d" % index)) for index in range(1, 7)]
    spells = [_int(entry.get("spell1Id")), _int(entry.get("spell2Id"))]
    cc = _stat(entry, "totalTimeCrowdControlDealt", 0)
    if not cc:
        cc = _stat(entry, "timeCCingOthers", 0)
    longest = _stat(entry, "longestTimeSpentLiving", None)
    return {
        "puuid": puuid,
        "team_id": _int(entry.get("teamId")),
        "champion_id": champion_id,
        "kills": _int(_stat(entry, "kills")),
        "deaths": _int(_stat(entry, "deaths")),
        "assists": _int(_stat(entry, "assists")),
        "damage": _int(_stat(entry, "totalDamageDealtToChampions")),
        "taken": _int(_stat(entry, "totalDamageTaken")),
        "heal": _int(_stat(entry, "totalHeal")),
        "cc": _float(cc),
        "gold": _int(_stat(entry, "goldEarned")),
        "items": items,
        "augments": augments,
        "spells": spells,
        "longest_time": _float(longest) if isinstance(longest, (int, float)) else None,
        "win": bool(_stat(entry, "win", False)),
    }


def normalize_game(raw, self_puuid=None):
    """Return a normalized game dict, or None when the payload has no players."""
    raw = _unwrap(raw)
    if not isinstance(raw, dict):
        return None
    raw_participants = _participants(raw)
    if not raw_participants:
        return None
    identities = _identities(raw)
    players = []
    for entry in raw_participants:
        player = _normalize_participant(entry, identities)
        if player:
            players.append(player)
    if not players:
        return None

    self_p = None
    if self_puuid:
        for player in players:
            if player.get("puuid") == self_puuid:
                self_p = player
                break
    if self_p is None:
        self_p = players[0]

    duration = _float(raw.get("gameDuration") or raw.get("gameDurationMillis") or 0)
    if duration > 100000:
        duration = duration / 1000.0
    created = _int(raw.get("gameCreation") or raw.get("gameCreationMillis") or 0)
    if not created:
        stamp = raw.get("gameCreationDate")
        if isinstance(stamp, str):
            try:
                created = int(time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")) * 1000)
            except Exception:
                created = 0

    game = {
        "game_id": raw.get("gameId") or raw.get("game_id"),
        "queue_id": _int(raw.get("queueId") or raw.get("queue_id")),
        "map_id": _int(raw.get("mapId") or raw.get("map_id")),
        "game_mode": raw.get("gameMode") or raw.get("game_mode"),
        "duration": duration,
        "created_at": created,
        "end_result": raw.get("endOfGameResult") or "",
        "self": self_p,
        "team": players if len(players) >= 4 else None,
    }
    return game


def is_aram(game):
    if not isinstance(game, dict):
        return False
    if game.get("duration") and game["duration"] < 300:
        return False
    if str(game.get("end_result") or "").startswith("GameAborted"):
        return False
    return (
        game.get("map_id") == ARAM_MAP
        or game.get("queue_id") in ARAM_QUEUES
        or str(game.get("game_mode") or "").upper() in ("KIWI", "ARAM")
    )


# ---------------------------------------------------------------------------
# raw collection


def _raw_games(puuid, count):
    # The local LCU match-history endpoint is much faster than SGP and accepts
    # other players' puuids. Prefer it; SGP remains the fallback when the
    # endpoint is unavailable.
    games = lcu.match_history(puuid, 0, count)
    if games:
        return games, "lcu", None, None

    payload = sgp.summary(puuid, count)
    if payload is not None and not (isinstance(payload, dict) and "__error" in payload):
        raws = sgp._games(payload)
        privacy = None
        if raws:
            private = any((game.get("metadata") or {}).get("private")
                          for game in raws if isinstance(game, dict))
            privacy = "PRIVATE" if private else "PUBLIC"
        return raws, "sgp", None, privacy

    error = payload.get("__error") if isinstance(payload, dict) else None
    return [], "none", error or "no-lcu-or-sgp", None


def _full_game(raw, puuid):
    raw = _unwrap(raw)
    if len(_participants(raw)) >= 10:
        return normalize_game(raw, puuid)
    game_id = raw.get("gameId") or raw.get("game_id")
    if game_id:
        detail = lcu.game_detail(game_id)
        if detail and len(_participants(detail)) >= 10:
            return normalize_game(detail, puuid)
        sgp_detail = sgp.details(game_id)
        if isinstance(sgp_detail, dict) and "__error" not in sgp_detail:
            if len(_participants(sgp_detail)) >= 10:
                return normalize_game(sgp_detail, puuid)
    return normalize_game(raw, puuid)


def _cached_game(game_id):
    if game_id is None:
        return None
    with _LOCK:
        cache = _load_cache()
        entry = (cache.get("games") or {}).get(str(game_id))
    if not isinstance(entry, dict):
        return None
    if time.time() - float(entry.get("_at") or 0) > GAME_TTL:
        return None
    game = entry.get("game")
    if isinstance(game, dict) and game.get("team"):
        return game
    return None


def _store_game(game):
    game_id = game.get("game_id") if isinstance(game, dict) else None
    if game_id is None:
        return
    with _LOCK:
        cache = _load_cache()
        cache["games"][str(game_id)] = {"_at": time.time(), "game": game}
        _save_cache()


def _raw_is_aram(raw):
    raw = _unwrap(raw)
    if not isinstance(raw, dict):
        return False
    duration = _float(raw.get("gameDuration") or raw.get("gameDurationMillis") or 0)
    if duration > 100000:
        duration = duration / 1000.0
    if duration and duration < 300:
        return False
    if str(raw.get("endOfGameResult") or "").startswith("GameAborted"):
        return False
    queue_id = _int(raw.get("queueId") or raw.get("queue_id"))
    map_id = _int(raw.get("mapId") or raw.get("map_id"))
    mode = str(raw.get("gameMode") or raw.get("game_mode") or "").upper()
    return map_id == ARAM_MAP or queue_id in ARAM_QUEUES or mode in ("KIWI", "ARAM")


def collect_games(puuid, count=20, limit=20):
    raws, source, error, privacy = _raw_games(puuid, count)
    pairs = []
    for raw in raws:
        inner = _unwrap(raw)
        if _raw_is_aram(inner):
            pairs.append((raw, inner))
    pairs.sort(key=lambda pair: _int(pair[1].get("gameCreation")
                                    or pair[1].get("gameCreationMillis") or 0), reverse=True)
    pairs = pairs[:limit]

    games = []
    for raw, inner in pairs:
        game_id = inner.get("gameId") or inner.get("game_id")
        game = _cached_game(game_id)
        if game is None:
            game = _full_game(inner, puuid)
            if game:
                _store_game(game)
        if game:
            games.append(game)
    games.sort(key=lambda game: game.get("created_at") or 0, reverse=True)
    return games, source, error, len(raws), len(pairs), privacy


def _privacy(puuid):
    data = lcu.summoner_by_puuid(puuid)
    if isinstance(data, dict):
        for key in ("privacy", "profilePrivacy", "privacyState"):
            value = data.get(key)
            if value:
                return str(value).upper()
    return None


def _level(summoner_id):
    if not summoner_id:
        return 0
    data = lcu.summoner(summoner_id)
    if isinstance(data, dict):
        return _int(data.get("summonerLevel"))
    return 0


# ---------------------------------------------------------------------------
# profile / analysis


def profile(puuid, count=20, level=0):
    """Return {games, reason, privacy, note, source, n} for one player."""
    if not puuid or puuid == ZERO_PUUID:
        return {"games": [], "reason": "anonymous", "privacy": None, "note": "", "source": "none", "n": 0}

    now = time.time()
    with _LOCK:
        cached = (_load_cache().get("profiles") or {}).get(puuid)
    if isinstance(cached, dict):
        cached_games = cached.get("games") or []
        ttl = PROFILE_TTL if cached_games else 5
        if now - float(cached.get("at") or 0) < ttl:
            return {
                "games": cached_games,
                "reason": cached.get("reason") or "",
                "privacy": cached.get("privacy"),
                "note": cached.get("note") or "",
                "source": cached.get("source") or "cache",
                "error": cached.get("error") or "",
                "n": len(cached_games),
            }

    games, source, error, raw_count, aram_count, privacy = collect_games(puuid, count=count)
    privacy = privacy or _privacy(puuid)

    reason = ""
    note = ""
    if not games:
        if raw_count > 0 and aram_count == 0:
            reason = "no_aram"
        elif privacy == "PRIVATE":
            reason = "private"
        else:
            reason = "unavailable"
        result = {"games": [], "reason": reason, "privacy": privacy, "note": note,
                  "source": source, "error": error, "n": 0}
        with _LOCK:
            cache = _load_cache()
            cache["profiles"][puuid] = {
                "at": now,
                "games": [],
                "reason": reason,
                "privacy": privacy,
                "note": note,
                "source": source,
                "error": error,
            }
            _save_cache()
        return result

    if len(games) < 3:
        reason = "no_aram"
    elif privacy == "PRIVATE":
        note = "生涯隐藏，数据来自 SGP"

    result = {
        "games": games,
        "reason": reason,
        "privacy": privacy,
        "note": note,
        "source": source,
        "error": error,
        "n": len(games),
    }
    with _LOCK:
        cache = _load_cache()
        cache["profiles"][puuid] = {
            "at": now,
            "games": games,
            "reason": reason,
            "privacy": privacy,
            "note": note,
            "source": source,
            "error": error,
        }
        _save_cache()
    return result


def _row_for(member, count, lookups_data):
    puuid = member.get("puuid")
    champion_id = _int(member.get("championId") or member.get("champion_id"))
    name = champion_name(champion_id)
    visible = member.get("nameVisibilityType")
    obfuscated = member.get("obfuscatedPuuid")

    if (not puuid or puuid == ZERO_PUUID
            or (visible == "HIDDEN" and obfuscated and puuid == obfuscated)):
        tags = tagrules.evaluate({}, {"reason": "anonymous"})
        return {
            "puuid": puuid,
            "champion_id": champion_id,
            "champion_name": name,
            "tags": tags,
            "n": 0,
            "avgAugmentPerf": None,
            "confidence": "—",
            "reason": "anonymous",
            "note": "选人阶段没有真实 puuid",
        }

    level = _level(member.get("summonerId"))
    prof = profile(puuid, count=count, level=level)
    metrics = tagrules.build_metrics(prof.get("games") or [], puuid, lookups_data)
    reason = prof.get("reason") or ""
    if reason == "unavailable" and level and level <= 50:
        reason = "new_account"
    context = {
        "reason": reason,
        "level": level,
        "full_team": metrics.get("full_team"),
        "new_account": bool(level and level <= 50),
    }
    tags = tagrules.evaluate(metrics, context, rules=tagrules.load_rules(DATA_DIR))
    note = prof.get("note") or ""
    if not note and prof.get("reason") == "unavailable":
        note = (prof.get("error") or "")[:120]
    return {
        "puuid": puuid,
        "champion_id": champion_id,
        "champion_name": name,
        "tags": tags,
        "n": metrics.get("n") or 0,
        "avgAugmentPerf": metrics.get("avgAugmentPerf"),
        "confidence": tags[0].get("confidence") if tags else "—",
        "reason": reason,
        "note": note,
        "source": prof.get("source") or "",
    }


def analyze_team(team, count=20, timeout=12.0, include_metrics=False):
    """Analyze the five allies from ``lcu.champ_select()``; never raises per row."""
    members = [member for member in (team or []) if isinstance(member, dict)]
    if not members:
        return []
    lookups_data = lookups()
    results = [None] * len(members)
    started = time.time()

    def work(index, member):
        try:
            row = _row_for(member, count, lookups_data)
            if include_metrics:
                prof = profile(member.get("puuid") or "", count=count)
                metrics = tagrules.build_metrics(prof.get("games") or [], member.get("puuid") or "", lookups_data)
                row["metrics"] = {
                    key: value
                    for key, value in metrics.items()
                    if key not in ("per_game", "champion_counts")
                }
            return index, row
        except Exception as error:
            return index, {
                "puuid": member.get("puuid"),
                "champion_id": _int(member.get("championId")),
                "champion_name": champion_name(_int(member.get("championId"))),
                "tags": tagrules.evaluate({}, {"reason": "unavailable"}),
                "n": 0,
                "avgAugmentPerf": None,
                "confidence": "—",
                "reason": "unavailable",
                "note": repr(error)[:120],
            }

    pool = ThreadPoolExecutor(max_workers=min(5, max(1, len(members))))
    futures = [(index, pool.submit(work, index, member)) for index, member in enumerate(members)]
    try:
        for index, future in futures:
            remaining = max(0.2, timeout - (time.time() - started))
            try:
                result_index, row = future.result(timeout=remaining)
            except Exception as error:
                result_index = index
                row = {
                    "puuid": members[index].get("puuid") if index < len(members) else None,
                    "champion_id": _int(members[index].get("championId")) if index < len(members) else 0,
                    "champion_name": champion_name(_int(members[index].get("championId")))
                    if index < len(members) else "?",
                    "tags": tagrules.evaluate({}, {"reason": "unavailable"}),
                    "n": 0,
                    "avgAugmentPerf": None,
                    "confidence": "—",
                    "reason": "unavailable",
                    "note": repr(error)[:120],
                }
            if 0 <= result_index < len(results):
                results[result_index] = row
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return [row for row in results if row]


# ---------------------------------------------------------------------------
# demo data (no client / no network)


def _demo_game(champion_id, wins, kills, deaths, assists, damage, taken, heal, cc, gold, items, index,
               team_damage=20000, team_taken=15000, team_heal=2000, team_cc=20,
               team_kills=4, team_assists=8):
    self_player = {
        "puuid": "demo-self",
        "team_id": 100,
        "champion_id": champion_id,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "damage": damage,
        "taken": taken,
        "heal": heal,
        "cc": cc,
        "gold": gold,
        "items": items + [0] * (7 - len(items)),
        "augments": [0] * 6,
        "spells": [32, 4],
        "longest_time": 60,
        "win": wins,
    }
    mates = []
    for mate in range(4):
        mates.append({
            "puuid": "demo-mate-%d" % mate,
            "team_id": 100,
            "champion_id": 1,
            "kills": team_kills,
            "deaths": 5,
            "assists": team_assists,
            "damage": team_damage,
            "taken": team_taken,
            "heal": team_heal,
            "cc": team_cc,
            "gold": 12000,
            "items": [0] * 7,
            "augments": [0] * 6,
            "spells": [4, 14],
            "longest_time": 60,
            "win": wins,
        })
    return {
        "game_id": 9000 + index,
        "queue_id": 2400,
        "map_id": 12,
        "game_mode": "KIWI",
        "duration": 1200,
        "created_at": int((time.time() - index * 1800) * 1000),
        "self": self_player,
        "team": [self_player] + mates,
    }


def demo_rows():
    lookups_data = lookups()
    profiles = [
        ("城墙", 111, [_demo_game(111, True, 3, 5, 15, 12000, 45000, 5000, 40, 14000,
                                  [3084, 3083, 3075, 3143, 3110, 3006], i) for i in range(12)]),
        ("人头狗", 103, [_demo_game(103, i % 2 == 0, 11, 6, 3, 14000, 15000, 2000, 20, 15000,
                                   [6655, 3020, 3089, 3135, 3157], i, team_damage=22000) for i in range(10)]),
        ("隐身哥", 99, [_demo_game(99, i % 3 == 0, 1, 6, 4, 9000, 10000, 3000, 15, 10000,
                                   [6655, 3020], i, team_damage=22000, team_taken=20000) for i in range(10)]),
        ("大爹", 222, [_demo_game(222, i < 8, 10, 5, 10, 45000, 15000, 2000, 10, 16000,
                                  [3031, 3006, 3072, 3085, 3036], i) for i in range(12)]),
        ("移动泉水", 16, [_demo_game(16, i < 6, 1, 4, 18, 6000, 14000, 30000, 20, 11000,
                                     [6617, 3504, 3107, 3222, 3190], i) for i in range(10)]),
    ]
    rows = []
    for name, champion_id, games in profiles:
        metrics = tagrules.build_metrics(games, "demo-self", lookups_data)
        tags = tagrules.evaluate(metrics, {"reason": "", "level": 200, "full_team": True},
                                 rules=tagrules.load_rules(DATA_DIR))
        rows.append({
            "puuid": "demo-" + str(champion_id),
            "champion_id": champion_id,
            "champion_name": name,
            "tags": tags,
            "n": metrics.get("n") or 0,
            "avgAugmentPerf": metrics.get("avgAugmentPerf"),
            "confidence": tags[0].get("confidence") if tags else "—",
            "reason": "",
            "note": "",
            "source": "demo",
        })
    return rows


# ---------------------------------------------------------------------------
# diagnostics


def probe():
    info = {"credentials": bool(lcu.credentials()), "summoner": None, "sources": {}}
    summoner = lcu.get("/lol-summoner/v1/current-summoner", timeout=2.0)
    if isinstance(summoner, dict):
        info["summoner"] = {
            "name": summoner.get("displayName") or summoner.get("gameName"),
            "puuid": summoner.get("puuid"),
            "platform": summoner.get("platformId") or summoner.get("rsoPlatformId"),
        }
    puuid = (info["summoner"] or {}).get("puuid")
    if puuid:
        raws, source, error = _raw_games(puuid, 5)
        info["sources"]["list"] = {"source": source, "count": len(raws), "error": error}
        if raws:
            raw = raws[0]
            game_id = raw.get("gameId") or raw.get("game_id")
            info["sources"]["lcu_list_participants"] = len(_participants(raw))
            detail = lcu.game_detail(game_id) if game_id else None
            info["sources"]["lcu_detail_participants"] = len(_participants(detail or {}))
            sgp_detail = sgp.details(game_id) if game_id else None
            info["sources"]["sgp_detail_participants"] = len(_participants(sgp_detail or {}))
    return info
