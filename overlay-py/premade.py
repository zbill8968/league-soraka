"""Premade (party) detection for the current champion select / game.

The client keeps the last game's ``gameData`` around while you sit in the lobby, so
reading it blindly shows the *previous* game's parties. Everything here therefore
validates the data against the current game before it is shown:

1. exact groups from ``/lol-gameflow/v1/session`` -> ``gameData.teamOne/teamTwo``,
   grouped by ``teamParticipantId``;
2. the payload is only trusted when its players match the current champion select
   (or, in game, when the champion ids match the live client);
3. otherwise we infer from shared recent matches and label the result as inferred.
"""

import time

import lcu
import sgp

_CACHE = {"at": 0.0, "value": None, "gameId": None}
_SELF = {"at": 0.0, "puuid": None}


def _players_by_team(game_data):
    """Return [(team_tag, player_dict), ...] using the array the player came from."""
    out = []
    for tag, key in ((100, "teamOne"), (200, "teamTwo")):
        for player in game_data.get(key) or []:
            if isinstance(player, dict):
                out.append((tag, player))
    return out


def self_puuid():
    now = time.time()
    if _SELF["puuid"] and now - _SELF["at"] < 600:
        return _SELF["puuid"]
    data = lcu.get("/lol-summoner/v1/current-summoner", timeout=2.0)
    puuid = (data or {}).get("puuid")
    if puuid:
        _SELF.update(at=now, puuid=puuid)
    return puuid


def game_session():
    session = lcu.get("/lol-gameflow/v1/session", timeout=2.0)
    return session if isinstance(session, dict) else None


def _matches_context(players, context_puuids=None, context_champions=None):
    if context_puuids:
        known = {p.get("puuid") for _, p in players}
        if not set(context_puuids).issubset(known):
            return False
    if context_champions:
        ids = {int(p.get("championId") or 0) for _, p in players}
        wanted = {int(c) for c in context_champions if c}
        if not wanted:
            return True
        overlap = len(wanted & ids) / len(wanted)
        if overlap < 0.8:
            return False
    return True


def detect(context_puuids=None, context_champions=None, retry_seconds=0.0):
    """Return premade info, or None when nothing trustworthy is available.

    {"groups": [{"team": 100|200, "side": "ally"|"enemy"|"unknown", "puuids": [...]}],
     "players": {puuid: {...}}, "gameId": int, "source": "gameflow"|"inferred"}
    """
    deadline = time.time() + max(0.0, retry_seconds)
    players = []
    session = None
    while True:
        session = game_session()
        game_data = (session or {}).get("gameData") or {}
        players = _players_by_team(game_data)
        if players and _matches_context(players, context_puuids, context_champions):
            break
        if time.time() >= deadline:
            players = []
            break
        time.sleep(0.4)

    if session is None or not players:
        return None

    # The client keeps the previous game's gameData while idle in the lobby; only
    # trust it while a draft or a game is actually running.
    phase = session.get("phase")
    if phase not in ("ChampSelect", "GameStart", "InProgress", "Reconnect"):
        return None

    # During a draft we need a reference, otherwise we cannot tell a fresh payload
    # from the previous game's leftovers. In game the payload is always the running
    # match (the stale case only happens in the lobby), so it can be trusted alone.
    if not context_puuids and not context_champions and phase in ("ChampSelect", "GameStart"):
        return None

    game_id = (session.get("gameData") or {}).get("gameId")
    me = self_puuid()
    info = {}
    by_group = {}
    party_ids = 0
    for tag, player in players:
        puuid = player.get("puuid")
        if not puuid or puuid == "00000000-0000-0000-0000-000000000000":
            continue
        info[puuid] = {
            "championId": int(player.get("championId") or 0),
            "team": tag,
            "name": player.get("summonerName") or "",
            "isSelf": bool(me and puuid == me),
        }
        group_id = player.get("teamParticipantId")
        if group_id in (None, 0, "0", ""):
            continue  # solo player: no party to report
        party_ids += 1
        by_group.setdefault((tag, group_id), []).append(puuid)

    self_team = None
    for puuid, meta in info.items():
        if meta.get("isSelf"):
            self_team = meta.get("team")
            break

    groups = []
    for (tag, _gid), puuids in by_group.items():
        if len(puuids) < 2:
            continue
        if self_team:
            side = "ally" if tag == self_team else "enemy"
        else:
            side = "unknown"
        groups.append({"team": tag, "side": side, "puuids": puuids})
    groups.sort(key=lambda g: (-len(g["puuids"]), g["team"]))

    value = {"groups": groups, "players": info, "gameId": game_id, "phase": phase,
             "selfTeam": self_team, "hasPartyIds": party_ids > 0, "source": "gameflow"}
    _CACHE.update(at=time.time(), value=value, gameId=game_id)
    return value


_INFER_CACHE = {}
_LAST_INFER = {}


def infer_from_history(puuids, count=50, min_shared=3):
    """Group players who shared a team in at least `min_shared` recent matches."""
    puuids = [p for p in puuids if p]
    if len(puuids) < 2:
        return []

    key = tuple(sorted(puuids))
    cached = _INFER_CACHE.get(key)
    if cached and time.time() - cached[0] < 120:
        _LAST_INFER.update(cached[2] if len(cached) > 2 else {})
        return cached[1]

    groups, meta = sgp.shared_games(puuids, count=count, min_shared=min_shared)
    _INFER_CACHE[key] = (time.time(), groups, meta)
    _LAST_INFER.clear()
    _LAST_INFER.update(meta)
    return groups


def infer_meta():
    return dict(_LAST_INFER)
