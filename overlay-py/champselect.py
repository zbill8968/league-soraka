"""Champion-select helper: match OCR text against the OP.GG champion list.

The Chinese client shows both the champion name and its title on the select
screen, and Data Dragon's zh_CN fields are not consistently name-vs-title, so a
champion is matched on any of: Chinese name, Chinese title, English name.
"""

import difflib
import json
import os
import re

_PUNCT = re.compile(r"[\s\u3000·・:：,，.。!！?？'\"“”‘’()（）\[\]【】\-_/\\]")


def norm(text):
    return _PUNCT.sub("", str(text or "")).lower()


def load_champions(data_dir):
    path = os.path.join(data_dir, "champions.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception:
        return {"total": 0, "champions": [], "index": {}}

    champions = raw.get("champions") or []
    index = {}
    for champ in champions:
        for key in (champ.get("nameZh"), champ.get("titleZh"), champ.get("nameEn")):
            text = norm(key)
            if len(text) >= 2:
                index[text] = champ
    by_id = {c["id"]: c for c in champions}
    return {"total": raw.get("total") or len(champions), "champions": champions, "index": index, "by_id": by_id}


def grade_for(rank, total):
    if rank is None:
        return "?"
    ratio = rank / max(1, total)
    if ratio <= 0.06:
        return "S"
    if ratio <= 0.15:
        return "A"
    if ratio <= 0.30:
        return "B"
    if ratio <= 0.55:
        return "C"
    return "D"


def match_lines(lines, catalogue, screen_height, screen_width):
    """Return unique champion matches with their screen position."""
    index = catalogue.get("index") or {}
    total = catalogue.get("total") or 0
    found = {}
    for line in lines:
        text = norm(line.get("text"))
        if len(text) < 2:
            continue
        champ = index.get(text)
        if champ is None:
            best_ratio, best_champ = 0.0, None
            for name, candidate in index.items():
                if abs(len(name) - len(text)) > 3:
                    continue
                ratio = difflib.SequenceMatcher(None, text, name).ratio()
                if ratio > best_ratio:
                    best_ratio, best_champ = ratio, candidate
            if best_ratio >= 0.8:
                champ = best_champ
        if champ is None and len(text) >= 2:
            # OCR often drops one or two characters of the big centre name
            # ("恶魔小丑" -> "恶丑"); accept a subsequence of a longer name.
            best_len, best_champ = 0, None
            for name, candidate in index.items():
                if len(name) <= len(text):
                    continue
                it = iter(name)
                if all(ch in it for ch in text) and len(name) > best_len:
                    best_len, best_champ = len(name), candidate
            if best_champ is not None:
                champ = best_champ
        if champ is None:
            continue
        key = champ["id"]
        y = float(line.get("y", 0))
        x = float(line.get("x", 0))
        # Champ select: the bench ("可用") sits in a strip along the top; your own
        # pick is the big name on the left half; everything else (team/enemy
        # columns on the sides) is not selectable and must be ignored.
        # Bench slots contain portraits only (no text), so OCR is used only for the
        # big champion name in the centre of the screen (your current pick).
        if screen_height * 0.48 < y < screen_height * 0.74 and screen_width * 0.28 < x < screen_width * 0.72:
            zone = "current"
        else:
            continue
        if key in found:
            if found[key]["zone"] == "current" and zone == "bench":
                continue
            if found[key]["zone"] == zone:
                continue
        found[key] = {
            "id": key,
            "name": champ.get("nameZh") or champ.get("nameEn"),
            "nameEn": champ.get("nameEn"),
            "rank": champ.get("rank"),
            "tier": champ.get("tier"),
            "grade": grade_for(champ.get("rank"), total),
            "zone": zone,
            "bench": zone == "bench",
            "x": x,
            "y": y,
        }
    bench = sorted((c for c in found.values() if c["zone"] == "bench"), key=lambda c: (c.get("rank") or 999))
    current = sorted((c for c in found.values() if c["zone"] == "current"), key=lambda c: (c.get("rank") or 999))
    return bench + current
