"""Scoring and pick policy, ported from lib/score.js and lib/policy.js.

The packaged build ships no Node.js, so the same logic runs in-process here. Keep
this file in sync with the JS modules; the unit tests in test/ are the reference.
"""

import bisect

RARITY_BY_CODE = {1: "silver", 2: "silver", 4: "gold", 8: "prismatic"}
RARITY_RANK = {"silver": 1, "gold": 2, "prismatic": 3}

CHAMPION_ALIASES = {
    "monkeyking": "wukong",
    "nunu": "nunuwillump",
    "renata": "renataglasc",
}

POLICY_PRESETS = {
    "conservative": {"style": "conservative", "minGap": 6, "upgradeGap": 10,
                     "chaseOnRarityUp": False, "protectPercentile": 0.9},
    "balanced": {"style": "balanced", "minGap": 4, "upgradeGap": 6,
                 "chaseOnRarityUp": True, "protectPercentile": 0.95},
    "aggressive": {"style": "aggressive", "minGap": 2, "upgradeGap": 3,
                   "chaseOnRarityUp": True, "protectPercentile": 1},
}


def normalize_champion_name(name):
    base = "".join(ch for ch in str(name or "").lower() if ch.isalnum() and ch.isascii())
    return CHAMPION_ALIASES.get(base, base)


def rarity_from_code(code):
    try:
        return RARITY_BY_CODE.get(int(code))
    except (TypeError, ValueError):
        return None


def grade_from_percentile(percentile):
    if percentile >= 0.95:
        return "S"
    if percentile >= 0.8:
        return "A"
    if percentile >= 0.5:
        return "B"
    if percentile >= 0.25:
        return "C"
    return "D"


def _clean_performance(value):
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return min(100.0, float(value))


def build_champion_model(stats_by_champion, slug):
    table = (stats_by_champion or {}).get(slug) or {}
    rows = []
    for augment_id, row in table.items():
        performance = _clean_performance((row or {}).get("performance"))
        if performance is None:
            continue
        rows.append((str(augment_id), performance, float((row or {}).get("popular") or 0)))
    rows.sort(key=lambda item: item[1])

    total = len(rows)
    by_augment = {}
    for index, (augment_id, performance, popular) in enumerate(rows):
        percentile = 0.5 if total <= 1 else index / (total - 1)
        by_augment[augment_id] = {"performance": performance, "popular": popular, "percentile": percentile}
    median = rows[len(rows) // 2][1] if rows else 50
    return {"slug": slug, "byAugment": by_augment, "poolSize": total, "median": median}


def percentile_for_score(model, score):
    """Percentile of a weighted score within the champion's OP.GG pool."""
    try:
        values = sorted(float(row["performance"]) for row in (model or {}).get("byAugment", {}).values())
        score = float(score)
    except (TypeError, ValueError, KeyError):
        return None
    if not values:
        return None
    if len(values) == 1:
        return 0.5
    pos = bisect.bisect_right(values, score) - 1
    return max(0.0, min(1.0, pos / (len(values) - 1)))


def score_augment(model, augment_id):
    row = (model or {}).get("byAugment", {}).get(str(augment_id))
    if not row:
        return {"score": None, "percentile": None, "grade": "?", "popular": 0, "known": False}
    return {
        "score": row["performance"],
        "percentile": row["percentile"],
        "grade": grade_from_percentile(row["percentile"]),
        "popular": row["popular"],
        "known": True,
    }


def rank_cards(cards):
    return sorted(cards, key=lambda card: (-(card.get("score") if card.get("score") is not None else -1),
                                           card.get("slot", 0)))


def _is_known(card):
    return card.get("score") is not None


def _rarity_rank(card):
    return RARITY_RANK.get(card.get("rarity") or "", 0)


def _is_protected(card, policy):
    percentile = card.get("percentile")
    return percentile is not None and percentile >= policy["protectPercentile"]


def _card_label(card):
    name = card.get("name")
    return f"{card['slot'] + 1} 号卡" + (f"「{name}」" if name else "")


def _build_reason(keep, reroll_slots, chase_slot, policy):
    score = keep.get("score")
    score_text = f" {score:.1f}" if score is not None else ""
    parts = [f"保留 {_card_label(keep)}（{keep.get('grade', '?')}{score_text}）"]
    if reroll_slots:
        parts.append("重置 " + "、".join(f"{slot + 1} 号卡" for slot in reroll_slots))
    else:
        parts.append("其余不建议重置")
    if chase_slot is not None:
        parts.append(f"重置结果更强，回手重置 {chase_slot + 1} 号卡去追")
    elif keep.get("origin") != "reroll":
        parts.append(f"若重置后出现高出 {policy['upgradeGap']} 分或更高稀有度的卡，再回手重置 {keep['slot'] + 1} 号卡")
    return "；".join(parts) + "。"


def recommend(cards, policy=None):
    policy = policy or POLICY_PRESETS["balanced"]
    if not cards:
        return {"keepSlot": -1, "rerollSlots": [], "chaseKeep": False, "chaseSlot": None,
                "finalSlot": None, "reason": "没有识别到卡片，重新截图试试。", "ranked": []}

    ranked = rank_cards(cards)
    known = [card for card in ranked if _is_known(card)]
    if not known:
        return {"keepSlot": ranked[0]["slot"], "rerollSlots": [], "chaseKeep": False, "chaseSlot": None,
                "finalSlot": ranked[0]["slot"], "ranked": ranked,
                "reason": "三张卡都没有可用数据（可能是新版本增幅），先按通用强度选，建议刷新数据。"}

    keep = known[0]
    keep_score = keep["score"]
    reroll_slots = [
        card["slot"] for card in ranked
        if card["slot"] != keep["slot"] and not card.get("rerollUsed")
        and (not _is_known(card) or card["score"] < keep_score - policy["minGap"])
    ]

    chase_slot = None
    if keep.get("origin") == "reroll":
        previous = [card for card in ranked
                    if card["slot"] != keep["slot"] and card.get("origin") != "reroll"
                    and not card.get("rerollUsed") and _is_known(card)]
        previous.sort(key=lambda card: -card["score"])
        if previous and not _is_protected(previous[0], policy):
            score_up = keep_score - previous[0]["score"] >= policy["upgradeGap"]
            rarity_up = policy["chaseOnRarityUp"] and _rarity_rank(keep) > _rarity_rank(previous[0])
            if score_up or rarity_up:
                chase_slot = previous[0]["slot"]

    return {
        "keepSlot": keep["slot"],
        "rerollSlots": reroll_slots,
        "chaseKeep": chase_slot is not None,
        "chaseSlot": chase_slot,
        "finalSlot": keep["slot"],
        "reason": _build_reason(keep, reroll_slots, chase_slot, policy),
        "ranked": ranked,
    }
