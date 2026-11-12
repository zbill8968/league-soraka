"""Fetch OP.GG ARAM: Mayhem item builds for the current champion.

The OP.GG page embeds its build widgets in the React Server Component stream.
We parse the five native "core items" rows from that stream and optionally ask
OP.GG's official MCP endpoint for the later high-win item candidates.

The result is cached in data/tmp so a game only pays the network cost once.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

PAGE = "https://op.gg/zh-cn/lol/modes/aram-mayhem/%s/build"
MCP = "https://mcp-api.op.gg/mcp"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
TMP_TTL = 6 * 3600


def _context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        "Accept": "text/html,application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_context()) as res:
        return res.read().decode("utf-8", "replace")


def _payload(html):
    parts = []
    for match in re.finditer(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html):
        try:
            parts.append(json.loads(match.group(1)))
        except Exception:
            continue
    return "".join(parts)


def _rows(payload, prefix):
    out = []
    for match in re.finditer(r'"' + re.escape(prefix) + r'_(\d+)"', payload):
        start = match.start()
        next_match = re.search(r'"' + re.escape(prefix) + r'_\d+"', payload[start + 1:])
        end = start + 1 + next_match.start() if next_match else payload.find("</tbody>", start)
        if end <= start:
            end = start + 6000
        block = payload[start:end]
        ids = [int(v) for v in re.findall(r'"metaId":(\d+)', block)]
        names = re.findall(r'"alt":"([^"]+)"', block)
        pairs = []
        for item_id, name in zip(ids, names):
            if item_id not in [p[0] for p in pairs]:
                pairs.append((item_id, name))
        if pairs:
            out.append([{"id": i, "name": n} for i, n in pairs])
    return out[:5]


def _mcp_candidates(slug):
    champion = re.sub(r"(?<!^)(?=[A-Z])", "_", slug).upper()
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "lol_get_champion_analysis",
            "arguments": {
                "game_mode": "aram",
                "champion": champion,
                "position": "mid",
                "lang": "zh_CN",
                "desired_output_fields": [],
            },
        },
    }
    req = urllib.request.Request(
        MCP,
        data=json.dumps(call).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_context()) as res:
            data = json.loads(res.read().decode("utf-8", "replace"))
        text = ((data.get("result") or {}).get("content") or [{}])[0].get("text") or ""
    except Exception:
        return []

    rows = []
    # Data order: core, boots, starter, last, fourth, fifth, sixth.  The later
    # three groups are the useful candidates; last is usually empty for ARAM.
    for match in re.finditer(
        r"CoreItems\(\[([^\]]*)\],\[((?:\"[^\"]*\"(?:,)?)*)\],(\d+),(\d+),([0-9.]+)\)", text):
        try:
            ids = [int(v) for v in match.group(1).split(",") if v.strip()]
            names = json.loads("[" + match.group(2) + "]")
            play, win = int(match.group(3)), int(match.group(4))
        except Exception:
            continue
        rows.append({"items": ids, "names": names, "winRate": (win / play) if play else None,
                     "play": play, "pickRate": float(match.group(5))})
    # First three are core/boots/starters; take the last nine and dedupe.
    later = []
    seen = set()
    for row in rows[3:]:
        key = tuple(row["items"])
        if key in seen:
            continue
        if any(item_id in (3020, 3158, 3111, 3006, 3047, 3117) for item_id in row["items"]):
            continue
        seen.add(key)
        later.append(row)
    return later[:5]


def fetch(data_dir, slug):
    if not slug:
        return None
    cache_dir = os.path.join(data_dir, "tmp")
    path = os.path.join(cache_dir, "builds-%s.json" % re.sub(r"[^a-z0-9_-]", "", slug.lower()))
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < TMP_TTL:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
    except Exception:
        pass

    html = _get(PAGE % urllib.parse.quote(slug))
    payload = _payload(html)
    core = _rows(payload, "core_items")
    boots = _rows(payload, "boots")
    starter = _rows(payload, "starter_items")
    if not core:
        return None
    result = {
        "champion": slug,
        "patch": "16.22",
        "fetchedAt": int(time.time()),
        "core": [{"items": [x["id"] for x in row], "names": [x["name"] for x in row]}
                 for row in core],
        "boots": [{"items": [x["id"] for x in row], "names": [x["name"] for x in row]}
                  for row in boots],
        "starters": [{"items": [x["id"] for x in row], "names": [x["name"] for x in row]}
                     for row in starter],
        "later": _mcp_candidates(slug),
    }
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False)
    except Exception:
        pass
    return result


def build_groups(data, limit=3):
    """Return up to three five-item builds with a reference win rate."""
    if not data:
        return []
    core = data.get("core") or []
    later = data.get("later") or []
    boots = data.get("boots") or []
    rates = {}
    for row in later:
        win = row.get("winRate")
        if win is not None:
            rates[int(row.get("items", [0])[0])] = float(win)
    groups = []
    for index, row in enumerate(core[:limit]):
        items = [int(x) for x in (row.get("items") or [])]
        names = list(row.get("names") or [])
        boot = boots[index % len(boots)] if boots else {}
        for item_id, name in zip(boot.get("items") or [], boot.get("names") or []):
            item_id = int(item_id)
            if item_id not in items:
                items.append(item_id)
                names.append(name)
                break
        for candidate in later:
            if len(items) >= 5:
                break
            item_id = int((candidate.get("items") or [0])[0])
            if item_id and item_id not in items:
                items.append(item_id)
                names.append((candidate.get("names") or [""])[0])
        for candidate in data.get("starters") or []:
            if len(items) >= 5:
                break
            for item_id, name in zip(candidate.get("items") or [], candidate.get("names") or []):
                item_id = int(item_id)
                if item_id not in items:
                    items.append(item_id)
                    names.append(name)
                    break
        wins = [rates[item] for item in items if item in rates]
        groups.append({
            "items": items[:5],
            "names": names[:5],
            "winRate": (sum(wins) / len(wins)) if wins else None,
        })
    return groups
