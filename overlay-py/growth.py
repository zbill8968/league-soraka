"""Level-based score multipliers for long-term growth augments."""

from __future__ import annotations

import json
import os

_CACHE = {"path": None, "mtime": None, "data": None}


def load(data_dir=None):
    path = os.path.join(data_dir or "", "growth-augments.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    if _CACHE["path"] == path and _CACHE["mtime"] == mtime and _CACHE["data"] is not None:
        return _CACHE["data"]
    data = {"version": 1, "multipliers": {}, "special": {}, "augments": {}}
    if mtime:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception:
            pass
    _CACHE.update(path=path, mtime=mtime, data=data)
    return data


def is_growth(data, augment_id):
    try:
        return str(int(augment_id)) in (data.get("augments") or {})
    except (TypeError, ValueError):
        return False


def multiplier(data, augment_id, level=None):
    """Return the score multiplier for one augment at the player's level."""
    try:
        augment_id = str(int(augment_id))
        level = int(level or 0)
    except (TypeError, ValueError):
        return 1.0
    if not is_growth(data, augment_id) or level <= 0:
        return 1.0
    special = (data.get("special") or {}).get(augment_id)
    if special:
        if level <= 10:
            return float(special.get("3-10", 1.0))
        if level <= 14:
            return float(special.get("11-14", 1.0))
        return float(special.get("15+", 1.0))
    table = data.get("multipliers") or {}
    if level <= 6:
        return float(table.get("3-6", 1.0))
    if level <= 10:
        return float(table.get("7-10", 1.0))
    if level <= 14:
        return float(table.get("11-14", 1.0))
    return float(table.get("15+", 1.0))


def name(data, augment_id):
    return (data.get("augments") or {}).get(str(augment_id)) or ""
