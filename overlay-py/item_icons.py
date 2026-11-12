"""Download and cache LoL item icons for the build panel."""

from __future__ import annotations

import json
import os
import ssl
import urllib.request

from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
FALLBACK_VERSION = "16.22.1"


def _context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _version(data_dir):
    try:
        with open(os.path.join(data_dir, "items.json"), "r", encoding="utf-8") as handle:
            return str(json.load(handle).get("version") or FALLBACK_VERSION)
    except Exception:
        return FALLBACK_VERSION


def _path(data_dir, item_id):
    return os.path.join(data_dir, "tmp", "item-icons", f"{int(item_id)}.png")


def _download(item_id, data_dir):
    dest = _path(data_dir, item_id)
    if os.path.exists(dest) and os.path.getsize(dest) > 100:
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    version = _version(data_dir)
    urls = [
        f"https://ddragon.leagueoflegends.com/cdn/{version}/img/item/{int(item_id)}.png",
        f"https://opgg-static.akamaized.net/meta/images/lol/latest/item/{int(item_id)}.png",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=8, context=_context()) as res:
                data = res.read()
            if len(data) > 100:
                tmp = dest + ".tmp"
                with open(tmp, "wb") as handle:
                    handle.write(data)
                os.replace(tmp, dest)
                return dest
        except Exception:
            continue
    return None


def prepare(builds, data_dir):
    """Attach local icon paths to every core/later row (parallel, cached)."""
    if not builds:
        return builds
    rows = []
    for section in ("core", "later", "boots", "starters"):
        rows.extend(builds.get(section) or [])
    ids = []
    for row in rows:
        for item_id in row.get("items") or []:
            try:
                item_id = int(item_id)
            except (TypeError, ValueError):
                continue
            if item_id not in ids:
                ids.append(item_id)
    paths = {}
    if ids:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_download, item_id, data_dir): item_id for item_id in ids}
            for future, item_id in futures.items():
                try:
                    paths[item_id] = future.result()
                except Exception:
                    paths[item_id] = None
    for row in rows:
        row["icons"] = [paths.get(int(item_id)) if str(item_id).isdigit() else None
                        for item_id in (row.get("items") or [])]
    return builds


def icon_map(builds):
    """Map item id -> cached icon path from all build sections."""
    out = {}
    for section in ("core", "later", "boots", "starters"):
        for row in builds.get(section) or []:
            for item_id, path in zip(row.get("items") or [], row.get("icons") or []):
                try:
                    out[int(item_id)] = path
                except (TypeError, ValueError):
                    continue
    return out
