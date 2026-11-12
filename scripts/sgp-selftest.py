"""One-shot SGP diagnostic: run elevated, writes .cache/sgp-selftest.json."""

import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "overlay-py"))

import lcu  # noqa: E402
import sgp  # noqa: E402

OUT = os.path.join(ROOT, ".cache", "sgp-selftest.json")
result = {"steps": []}


def step(name, fn):
    try:
        value = fn()
        result["steps"].append({"name": name, "ok": True, "value": value})
        return value
    except Exception as error:
        result["steps"].append({"name": name, "ok": False, "error": repr(error),
                                "trace": traceback.format_exc()[-600:]})
        return None


def compact_summary(puuid):
    payload = sgp.summary(puuid, 5)
    if payload is None:
        return {"payload": None}
    if isinstance(payload, dict) and "__error" in payload:
        return {"payload": "error", "error": payload["__error"]}
    if isinstance(payload, list):
        return {"payload": "list", "count": len(payload),
                "first_keys": sorted(payload[0].keys())[:20] if payload and isinstance(payload[0], dict) else None}
    if isinstance(payload, dict):
        games = payload.get("games")
        info = {"payload": "dict", "top_keys": sorted(payload.keys())[:20]}
        if isinstance(games, dict):
            info["games_type"] = "dict"
            info["games_keys"] = sorted(games.keys())[:8]
            nested = games.get("games")
            if isinstance(nested, list) and nested:
                info["game_count"] = len(nested)
                info["game_keys"] = sorted(nested[0].keys())[:20]
                participants = nested[0].get("participants")
                if isinstance(participants, list) and participants:
                    info["participant_keys"] = sorted(participants[0].keys())[:20]
        elif isinstance(games, list):
            info["games_type"] = "list"
            info["game_count"] = len(games)
            if games and isinstance(games[0], dict):
                info["game_keys"] = sorted(games[0].keys())[:20]
        return info
    return {"payload": type(payload).__name__}


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    creds = step("credentials", lambda: list(lcu.credentials() or ()))
    puuid = step("puuid", lambda: (lcu.get("/lol-summoner/v1/current-summoner", timeout=3.0) or {}).get("puuid"))
    step("phase", lambda: (lcu.get("/lol-gameflow/v1/gameflow-phase", timeout=3.0)))
    step("platform", sgp.platform)
    step("host", sgp.host)
    token = step("league_token", lambda: (sgp.league_token() or "")[:24])
    result["token_present"] = bool(token)
    if puuid:
        step("summary", lambda: compact_summary(puuid))
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
