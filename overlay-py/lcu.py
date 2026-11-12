"""League Client (LCU) access for exact champion-select data.

The Tencent client does not write a usable lockfile, but its command line carries
``--app-port`` and ``--remoting-auth-token``. Two ways to read it:

1. WMI (requires this process to be elevated);
2. the client's own ``*_LeagueClientUx.log``, whose first line echoes that command
   line — readable without elevation, which is what makes the packaged build work
   for users who do not want to run as administrator.
"""

import base64
import json
import os
import re
import ssl
import subprocess
import time

CREATE_NO_WINDOW = 0x08000000
CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "LeagueSoraka")

_ARGS_TOKEN = re.compile(r"--remoting-auth-token=([\w\-]+)")
_ARGS_PORT = re.compile(r"--app-port=(\d+)")
_ARGS_PLATFORM = re.compile(r"--rso_platform_id=([A-Za-z0-9_]+)")


def _command_line():
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='LeagueClientUx.exe'\").CommandLine"],
            capture_output=True, text=True, timeout=8, creationflags=CREATE_NO_WINDOW,
        )
        return proc.stdout or ""
    except Exception:
        return ""


def _client_dirs():
    """Candidate LeagueClient install folders, cached after the first hit."""
    cache_file = os.path.join(CACHE_DIR, "client-path.json")
    try:
        with open(cache_file, "r", encoding="utf-8") as handle:
            cached = (json.load(handle) or {}).get("dir")
        if cached and os.path.isdir(cached):
            yield cached
    except Exception:
        pass

    candidates = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env)
        if base:
            candidates.append(os.path.join(base, "WeGameApps", "英雄联盟", "LeagueClient"))
            candidates.append(os.path.join(base, "Riot Games", "League of Legends", "LeagueClient"))
    for drive in ("C:", "D:", "E:", "F:", "G:"):
        candidates.append(os.path.join(drive + os.sep, "WeGameApps", "英雄联盟", "LeagueClient"))
        candidates.append(os.path.join(drive + os.sep, "Riot Games", "League of Legends", "LeagueClient"))
        candidates.append(os.path.join(drive + os.sep, "Program Files", "WeGameApps", "英雄联盟", "LeagueClient"))
        candidates.append(os.path.join(drive + os.sep, "娱乐", "WeGameApps", "英雄联盟", "LeagueClient"))
    for path in candidates:
        if os.path.isdir(path):
            yield path


def _from_logs():
    """Parse the newest UX log; returns (line, dir) or (None, None)."""
    best = (None, None)
    for folder in _client_dirs():
        try:
            logs = [os.path.join(folder, name) for name in os.listdir(folder)
                    if name.endswith("LeagueClientUx.log")]
        except Exception:
            continue
        logs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for path in logs[:4]:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    head = handle.read(20000)
            except Exception:
                continue
            for line in head.splitlines():
                if _ARGS_TOKEN.search(line) and _ARGS_PORT.search(line):
                    return line, folder
    return best


def _remember_dir(folder):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, "client-path.json"), "w", encoding="utf-8") as handle:
            json.dump({"dir": folder}, handle, ensure_ascii=False)
    except Exception:
        pass


_CREDS = {"at": 0.0, "value": None, "platform": None}


def credentials(max_age=60.0):
    """Cached client credentials plus platform id."""
    now = time.time()
    if _CREDS["value"] and now - _CREDS["at"] < max_age:
        return _CREDS["value"]

    line = _command_line()
    if not (_ARGS_PORT.search(line) and _ARGS_TOKEN.search(line)):
        line, folder = _from_logs()
        if folder:
            _remember_dir(folder)

    port = _ARGS_PORT.search(line or "")
    token = _ARGS_TOKEN.search(line or "")
    platform = _ARGS_PLATFORM.search(line or "")
    if not port or not token:
        return None
    value = (int(port.group(1)), token.group(1))
    _CREDS.update(at=now, value=value, platform=(platform.group(1) if platform else None))
    return value


def platform():
    """Platform id such as TJ101, parsed from the same command line."""
    if not _CREDS["value"]:
        credentials()
    return _CREDS.get("platform")


def get(path, timeout=2.5):
    creds = credentials()
    if not creds:
        return None
    port, token = creds
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    auth = base64.b64encode(("riot:" + token).encode()).decode()
    try:
        import urllib.request
        req = urllib.request.Request(f"https://127.0.0.1:{port}{path}", headers={"Authorization": "Basic " + auth})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as res:
            body = res.read().decode("utf-8", "replace")
        return json.loads(body) if body else None
    except Exception:
        return None


def parse_bench(raw):
    """Normalize benchChampions ([{championId}]) or benchChampionIds ([int])."""
    bench = []
    for item in raw or []:
        if isinstance(item, dict):
            cid = item.get("championId")
        else:
            cid = item
        if cid:
            bench.append(int(cid))
    return bench


def champ_select():
    """Return the champion-select session in a compact, prefetch-friendly shape."""
    session = get("/lol-champ-select/v1/session")
    if not session:
        return None
    local_cell = session.get("localPlayerCellId")
    current = None
    for member in session.get("myTeam") or []:
        if member.get("cellId") == local_cell and member.get("championId"):
            current = int(member["championId"])
            break

    bench = parse_bench(session.get("benchChampions") or session.get("benchChampionIds"))

    team = []
    for member in session.get("myTeam") or []:
        puuid = member.get("puuid")
        if not puuid:
            continue
        team.append({"puuid": puuid,
                     "championId": int(member.get("championId") or 0),
                     "cellId": member.get("cellId"),
                     "summonerId": member.get("summonerId"),
                     "nameVisibilityType": member.get("nameVisibilityType"),
                     "obfuscatedPuuid": member.get("obfuscatedPuuid")})
    return {
        "current": current,
        "bench": bench,
        "benchEnabled": bool(session.get("benchEnabled")),
        "benchChampionIds": bench,
        "gameId": session.get("gameId"),
        "team": team,
    }


def gameflow_phase():
    """Current gameflow phase ("ChampSelect", "InProgress", ...) or None."""
    return get("/lol-gameflow/v1/gameflow-phase")


def champ_select_key(session):
    """Stable per-session key: gameId + the five sorted puuids."""
    team = (session or {}).get("team") or []
    puuids = tuple(sorted(str(member.get("puuid")) for member in team if member.get("puuid")))
    return ((session or {}).get("gameId"), puuids)


def is_aram_champ_select(session):
    """ARAM/Mayhem has a champion bench; SR draft does not."""
    if not isinstance(session, dict):
        return False
    return bool(session.get("benchEnabled") or session.get("bench") or session.get("benchChampionIds"))


def champ_select_ready(session):
    """Five allies and all five have a champion assigned (avoids '?' rows)."""
    team = (session or {}).get("team") or []
    return len(team) >= 5 and all(member.get("championId") for member in team)


def _quote(value):
    from urllib.parse import quote
    return quote(str(value), safe="")


def match_history(puuid, beg=0, end=30):
    """Recent games for a player. For other players the list has one participant."""
    data = get("/lol-match-history/v1/products/lol/%s/matches?begIndex=%d&endIndex=%d"
               % (_quote(puuid), int(beg), int(end)))
    if isinstance(data, dict):
        games = data.get("games")
        if isinstance(games, dict):
            return games.get("games") or []
        if isinstance(games, list):
            return games
    if isinstance(data, list):
        return data
    return []


def game_detail(game_id):
    """Full game payload for one game id (10 participants when available)."""
    return get("/lol-match-history/v1/games/%s" % _quote(game_id))


def summoner(summoner_id):
    return get("/lol-summoner/v1/summoners/%s" % _quote(summoner_id))


def summoner_by_puuid(puuid):
    return get("/lol-summoner/v1/summoners-by-puuid-cached/%s" % _quote(puuid))
