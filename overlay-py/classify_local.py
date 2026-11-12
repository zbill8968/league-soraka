"""In-process augment classifier (replaces the Node classifier for packaged builds).

Matching order mirrors scripts/classify-core.mjs: OCR'd Chinese name first, then the
icon descriptor stored in data/icon-index.json when the crop is available.
"""

import difflib
import json
import os
import re

import growth
import scoring

_CACHE = {}


def _load(data_dir):
    def mtime(name):
        path = os.path.join(data_dir, name)
        return os.path.getmtime(path) if os.path.exists(path) else 0

    key = (data_dir, mtime("stats.json"), mtime("catalog.json"), mtime("growth-augments.json"))
    if _CACHE.get("key") == key:
        return _CACHE["value"]

    def read(name, fallback):
        try:
            with open(os.path.join(data_dir, name), "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return fallback

    catalog = read("catalog.json", {"augments": [], "champions": []})
    stats = read("stats.json", {"byChampion": {}})
    index = read("icon-index.json", {"items": []})
    growth_data = growth.load(data_dir)
    by_id = {item["id"]: item for item in catalog.get("augments") or []}
    value = {"catalog": catalog, "stats": stats, "index": index, "byId": by_id,
             "growth": growth_data}
    _CACHE["key"] = key
    _CACHE["value"] = value
    return value


_PUNCT = set(" \u3000·・:：,，.。!！?？'\"“”‘’()（）[]【】-_/\\")


def _norm(text):
    return "".join(ch for ch in str(text or "").lower() if ch not in _PUNCT)


def _levenshtein(a, b):
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if ca == cb else 1)))
        prev = cur
    return prev[-1]


def _match_name(text, augments):
    needle = _norm(text)
    if not needle:
        return None
    best = None
    for augment in augments:
        for name in (augment.get("nameZh"), augment.get("name")):
            hay = _norm(name)
            if not hay:
                continue
            if hay == needle:
                return {"id": augment["id"], "ratio": 1.0}
            ratio = 1 - _levenshtein(needle, hay) / max(len(needle), len(hay))
            if ratio >= 0.62 and (best is None or ratio > best["ratio"]):
                best = {"id": augment["id"], "ratio": ratio}
    if best is None and len(needle) >= 2:
        # OCR often drops characters of long augment names.
        longest = None
        for augment in augments:
            hay = _norm(augment.get("nameZh"))
            if not hay or len(hay) <= len(needle):
                continue
            pos = 0
            for ch in needle:
                pos = hay.find(ch, pos)
                if pos < 0:
                    break
                pos += 1
            else:
                if longest is None or len(hay) > len(_norm(longest.get("nameZh"))):
                    longest = augment
        if longest is not None:
            best = {"id": longest["id"], "ratio": 0.7}
    return best


def _desc_norm(text):
    text = re.sub(r"<[^>]+>", "", str(text or ""))
    return "".join(ch.lower() for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _match_description(text, augments):
    """Fallback for in-game names that differ from OP.GG's translation.

    The OCR'd description is matched against OP.GG's Chinese ``descZh`` by its
    longest common substring. Only a reasonably long, distinctive match is used.
    """
    needle = _desc_norm(text)
    if len(needle) < 8:
        return None
    best = None
    for augment in augments:
        for key in ("descZh", "desc"):
            hay = _desc_norm(augment.get(key))
            if len(hay) < 8:
                continue
            match = difflib.SequenceMatcher(None, needle, hay).find_longest_match(
                0, len(needle), 0, len(hay))
            if match.size < 6:
                continue
            ratio = match.size / len(needle)
            if ratio >= 0.5 and (best is None or ratio > best["ratio"]):
                best = {"id": augment["id"], "ratio": ratio}
    return best


def _global_model(stats):
    sums, counts = {}, {}
    for table in (stats.get("byChampion") or {}).values():
        for augment_id, row in table.items():
            value = (row or {}).get("performance")
            if not isinstance(value, (int, float)) or value <= 0:
                continue
            sums[augment_id] = sums.get(augment_id, 0) + value
            counts[augment_id] = counts.get(augment_id, 0) + 1
    table = {augment_id: {"performance": total / (counts.get(augment_id) or 1)} for augment_id, total in sums.items()}
    return scoring.build_champion_model({"__global__": table}, "__global__")


def classify(data_dir, champion=None, names=None, crops=None, policy="balanced", descriptions=None, level=None):
    data = _load(data_dir)
    catalog, stats, by_id = data["catalog"], data["stats"], data["byId"]
    augments = catalog.get("augments") or []
    growth_data = data.get("growth") or {}
    warnings = []

    slug = scoring.normalize_champion_name(champion) if champion else None
    global_model = _global_model(stats)
    model = scoring.build_champion_model(stats.get("byChampion"), slug) if slug else None
    mode = "champion"
    if not model or model["poolSize"] == 0:
        if slug:
            warnings.append(f'no OP.GG data for champion "{champion}" ({slug}); using global averages')
        model = global_model
        mode = "global"
        warnings.append("no champion detected; grades use cross-champion averages")

    names = list(names or [])
    descriptions = list(descriptions or [])
    cards = []
    for slot in range(3):
        name_text = names[slot] if slot < len(names) else ""
        desc_text = descriptions[slot] if slot < len(descriptions) else ""
        match = _match_name(name_text, augments) if name_text else None
        matched_by = "name"
        # OP.GG's translation sometimes differs from the live client. If the name
        # is missing/weak, identify the augment by the description under the card.
        if not match or match["ratio"] < 0.90:
            desc_match = _match_description(desc_text, augments)
            if desc_match and (not match or desc_match["ratio"] > match["ratio"]):
                match = desc_match
                matched_by = "desc"
                warnings.append(f"slot {slot + 1}: 名称不一致，按说明匹配")
        if match:
            augment = by_id.get(match["id"]) or {}
            scored = scoring.score_augment(model, match["id"])
            score_source = mode
            if scored["score"] is None:
                # The champion table may not list this augment; use the
                # cross-champion average rather than showing "?".
                alt = scoring.score_augment(global_model, match["id"])
                if alt["score"] is not None:
                    scored = alt
                    score_source = "global"
                    warnings.append(f"slot {slot + 1}: 英雄表无此增幅，使用全英雄平均")
            selected_model = model if score_source == "champion" else global_model
            base_score = scored["score"]
            multiplier = growth.multiplier(growth_data, match["id"], level)
            score = base_score
            percentile = scored["percentile"]
            grade = scored["grade"]
            if base_score is not None and multiplier != 1.0:
                score = min(100.0, float(base_score) * multiplier)
                percentile = scoring.percentile_for_score(selected_model, score)
                if percentile is not None:
                    grade = scoring.grade_from_percentile(percentile)
            cards.append({
                "slot": slot,
                "augmentId": match["id"],
                "name": augment.get("nameZh") or augment.get("name") or "未识别",
                "nameEn": augment.get("name"),
                "rarity": scoring.rarity_from_code(augment.get("rarity")),
                "score": score,
                "baseScore": base_score,
                "growth": bool(multiplier != 1.0 or growth.is_growth(growth_data, match["id"])),
                "growthMultiplier": multiplier,
                "percentile": percentile,
                "grade": grade,
                "scoreSource": score_source,
                "confidence": round(min(1.0, match["ratio"]), 3),
                "matchedBy": matched_by,
                "ocr": name_text,
                "ocrDesc": desc_text[:120],
            })
            continue
        cards.append({"slot": slot, "augmentId": None, "name": "未识别", "rarity": None, "score": None,
                      "percentile": None, "grade": "?", "confidence": 0, "ocr": name_text})

    preset = scoring.POLICY_PRESETS.get(policy, scoring.POLICY_PRESETS["balanced"])
    return {
        "champion": champion,
        "mode": mode,
        "patch": catalog.get("patch"),
        "dataAge": stats.get("fetchedAt"),
        "cards": cards,
        "recommendation": scoring.recommend(cards, preset),
        "warnings": warnings,
    }
