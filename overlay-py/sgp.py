"""SGP match-history client.

The League client talks to Riot's SGP service for match history. With the client's
own entitlements (or league-session) token we can ask for a player's recent matches,
which is what tools like League Akari use to infer premade groups (the local
``/lol-match-history`` endpoint only returns the queried player's own entry).

Endpoints (Akari-compatible):
  GET /entitlements/v1/token                            -> entitlements token (primary)
  GET /lol-league-session/v1/league-session-token       -> session token (fallback)
  GET https://{sgpHost}/match-history-query/v1/products/lol/player/{puuid}/SUMMARY

Each SUMMARY item is ``{metadata, json}``; ``json`` is the full 10-player game,
so one request per player replaces a match list plus 20 detail calls.
"""

import base64
import json
import os
import ssl
import threading
import time
import urllib.request

import lcu

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVERS_FILE = os.path.join(ROOT, "data", "sgp-servers.json")

_CACHE = {"token": None, "at": 0.0, "ent_token": None, "ent_at": 0.0, "host": None, "region": None}
_TLS = ssl.create_default_context()
_SUMMARY_LOCK = threading.Lock()
_TLS.check_hostname = False
_TLS.verify_mode = ssl.CERT_NONE


def servers():
    try:
        with open(SERVERS_FILE, "r", encoding="utf-8") as handle:
            return (json.load(handle) or {}).get("servers") or {}
    except Exception:
        return {}


def _jwt_payload(token):
    try:
        part = str(token).split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part).decode("utf-8", "replace"))
    except Exception:
        return {}


def platform():
    """Platform id such as TJ101 / EUW1, used to pick the SGP host."""
    cached = getattr(lcu, "platform", lambda: None)()
    if cached:
        return str(cached).upper()
    data = lcu.get("/lol-summoner/v1/current-summoner", timeout=2.0) or {}
    for key in ("platformId", "rsoPlatformId", "platform"):
        value = data.get(key)
        if value:
            return str(value).upper()
    payload = _jwt_payload(league_token() or "")
    for key in ("dat", "region", "r", "platform"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value.upper()
    return None


def host():
    if _CACHE["host"]:
        return _CACHE["host"]
    table = servers()
    region = platform()
    key = None
    if region:
        for candidate in (f"TENCENT_{region}", region, region.rstrip("1")):
            if candidate in table:
                key = candidate
                break
    if key is None and "TENCENT_TJ101" in table and (region or "").startswith("TJ10"):
        key = "TENCENT_TJ101"
    if key is None:
        return None
    entry = table[key] or {}
    url = entry.get("matchHistory") or entry.get("common")
    if not url:
        return None
    _CACHE["host"] = url.rstrip("/")
    _CACHE["region"] = key
    return _CACHE["host"]


def league_token(max_age=900):
    now = time.time()
    if _CACHE["token"] and now - _CACHE["at"] < max_age:
        return _CACHE["token"]
    data = lcu.get("/lol-league-session/v1/league-session-token", timeout=2.5)
    token = None
    if isinstance(data, str):
        token = data
    elif isinstance(data, dict):
        for key in ("token", "accessToken", "access_token", "value"):
            if isinstance(data.get(key), str) and data[key]:
                token = data[key]
                break
    if token:
        _CACHE.update(token=token, at=now)
    return token


def entitlements_token(max_age=900):
    """The token Akari uses for match-history-query (entitlements, not session)."""
    now = time.time()
    if _CACHE["ent_token"] and now - _CACHE["ent_at"] < max_age:
        return _CACHE["ent_token"]
    data = lcu.get("/entitlements/v1/token", timeout=2.5)
    token = None
    if isinstance(data, dict):
        for key in ("accessToken", "access_token", "token"):
            value = data.get(key)
            if isinstance(value, str) and value:
                token = value
                break
    if token:
        _CACHE.update(ent_token=token, ent_at=now)
    return token


def _get(url, token, timeout=5.0):
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(request, timeout=timeout, context=_TLS) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def summary(puuid, count=20):
    """Return the raw SUMMARY payload for one player, or None."""
    base = host()
    # Akari uses the entitlements token for match-history-query; the
    # league-session token is kept as a fallback for clients that lack it.
    token = entitlements_token() or league_token()
    if not base or not token or not puuid:
        return None
    url = (f"{base}/match-history-query/v1/products/lol/player/{puuid}/SUMMARY"
           f"?startIndex=0&count={count}")
    try:
        return _get(url, token, timeout=20.0)
    except Exception as error:
        return {"__error": repr(error)}


def details(game_id, platform_id=None):
    """Return the raw DETAILS payload for one game, or {'__error': ...}.

    SGP prefixes the product id with the platform (TJ101_401182541334). Some
    deployments accept the bare game id, so try both instead of guessing.
    """
    base = host()
    token = entitlements_token() or league_token()
    if not base or not token or not game_id:
        return {"__error": "no-host-or-token"}
    platform_id = (platform_id or platform() or "").upper()
    names = []
    if platform_id:
        names.append(f"{platform_id}_{game_id}")
    names.append(str(game_id))
    last = None
    for name in names:
        url = f"{base}/match-history-query/v1/products/lol/{name}/DETAILS"
        try:
            return _get(url, token)
        except Exception as error:
            last = error
    return {"__error": repr(last)}


def _games(payload):
    if isinstance(payload, list):
        return [g for g in payload if isinstance(g, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("games", "matchHistory", "summaries"):
        value = payload.get(key)
        if isinstance(value, list):
            return [g for g in value if isinstance(g, dict)]
        if isinstance(value, dict):
            nested = value.get("games")
            if isinstance(nested, list):
                return [g for g in nested if isinstance(g, dict)]
    return []


def unwrap(item):
    """Turn a SUMMARY item ({metadata, json}) into the full game dict.

    The ``json`` field is a full 10-player game (flat stats), so one SUMMARY
    request per player is enough for damage/taken shares — no 20 DETAILS calls.
    """
    if not isinstance(item, dict):
        return item
    inner = item.get("json")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except Exception:
            inner = None
    if isinstance(inner, dict):
        inner = dict(inner)
        meta = item.get("metadata") or {}
        inner["_private"] = bool(meta.get("private"))
        inner["_match_id"] = meta.get("match_id")
        inner["_tags"] = meta.get("tags") or []
        return inner
    return item


def _game_teams(game):
    """Return {teamId: [puuid, ...]} from a SUMMARY item."""
    inner = game.get("json")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except Exception:
            inner = None
    teams = {}
    if isinstance(inner, dict):
        for item in inner.get("participants") or []:
            if not isinstance(item, dict):
                continue
            puuid = item.get("puuid")
            team_id = item.get("teamId")
            if puuid and team_id is not None:
                teams.setdefault(team_id, []).append(puuid)
    if teams:
        return teams
    meta = game.get("metadata")
    if isinstance(meta, dict):
        members = [p for p in meta.get("participants") or [] if isinstance(p, str)]
        if members:
            teams.setdefault(0, []).extend(members)
    return teams


def _game_members(game):
    members = set()
    for roster in _game_teams(game).values():
        members.update(roster)
    return members


def shared_games(puuids, count=30, min_shared=3):
    """Group players who appeared on the same team in >= min_shared recent matches."""
    puuids = [p for p in puuids if p]
    if len(puuids) < 2:
        return [], {"reason": "too-few-puuids"}

    seen = {}
    errors = []
    for puuid in puuids:
        payload = summary(puuid, count)
        if payload is None:
            errors.append(f"{puuid[:8]}:no-host-or-token")
            continue
        if isinstance(payload, dict) and "__error" in payload:
            errors.append(f"{puuid[:8]}:{payload['__error'][:60]}")
            continue
        for game in _games(payload):
            for roster in _game_teams(game).values():
                present = [p for p in puuids if p in roster]
                if len(present) < 2:
                    continue
                for i in range(len(present)):
                    for j in range(i + 1, len(present)):
                        key = tuple(sorted((present[i], present[j])))
                        seen.setdefault(key, set()).add(game.get("gameId"))

    pairs = [key for key, games in seen.items() if len(games) >= min_shared]
    if not pairs:
        return [], {"reason": "no-shared", "pairs": len(seen), "errors": errors[:3]}

    parent = {p: p for p in puuids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    merged = {}
    for puuid in puuids:
        merged.setdefault(find(puuid), []).append(puuid)
    groups = [members for members in merged.values() if len(members) > 1]
    groups.sort(key=len, reverse=True)
    return groups, {"reason": "ok", "pairs": len(pairs), "errors": errors[:3]}
