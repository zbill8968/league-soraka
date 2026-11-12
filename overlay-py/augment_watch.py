"""Pure helpers for detecting a rerolled augment offer.

The signature is three 16x16 grayscale crops (one per card), concatenated.
No Tk/PIL imports here so the logic is unit-testable with plain Python.
"""

CARD_SIZE = 16 * 16
DEFAULT_THRESHOLD = 12.0
STABLE_TOLERANCE = 6.0


def _cards(signature):
    signature = bytes(signature or b"")
    return [signature[i * CARD_SIZE:(i + 1) * CARD_SIZE] for i in range(3)]


def card_diffs(a, b):
    """Mean absolute per-pixel difference for each of the three cards."""
    left = _cards(a)
    right = _cards(b)
    out = []
    for x, y in zip(left, right):
        if len(x) != CARD_SIZE or len(y) != CARD_SIZE:
            out.append(255.0)
            continue
        out.append(sum(abs(p - q) for p, q in zip(x, y)) / CARD_SIZE)
    return out


def max_diff(a, b):
    if not a or not b:
        return 255.0
    return max(card_diffs(a, b))


def changed(a, b, threshold=DEFAULT_THRESHOLD):
    if not a or not b:
        return False
    return max_diff(a, b) >= threshold


def stable(a, b, tolerance=STABLE_TOLERANCE):
    return max_diff(a, b) <= tolerance
