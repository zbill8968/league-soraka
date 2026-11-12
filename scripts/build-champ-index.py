"""Build data/champ-index.json: perceptual descriptors for champion portraits.

Champion-select bench slots show only portraits, no text, so the bench has to be
identified by image. This script downloads Data Dragon square icons once and stores
the same coverage+colour descriptor used by the augment matcher.
"""

import hashlib
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
ICONS = os.path.join(DATA, "champ-icons")
GRID = 16


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


def describe_rgba(rgba, width, height):
    # Same algorithm as lib/hash.js: art bounding box, then per-cell coverage+colour.
    min_x, min_y, max_x, max_y = width, height, -1, -1
    for y in range(height):
        for x in range(width):
            p = (y * width + x) * 4
            a = rgba[p + 3] / 255
            luma = 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2]
            if a > 0.15 or luma > 60:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
    if max_x < min_x or max_y < min_y:
        min_x, min_y, max_x, max_y = 0, 0, width - 1, height - 1
    pad = int(max(max_x - min_x, max_y - min_y) * 0.05)
    x0 = max(0, min_x - pad)
    y0 = max(0, min_y - pad)
    x1 = min(width, max_x + 1 + pad)
    y1 = min(height, max_y + 1 + pad)
    bw, bh = max(4, x1 - x0), max(4, y1 - y0)

    cov = [0.0] * (GRID * GRID)
    rgb = [0.0] * (GRID * GRID * 3)
    total = [0] * (GRID * GRID)
    art = [0] * (GRID * GRID)
    for y in range(y0, y0 + bh):
        gy = min(GRID - 1, (y - y0) * GRID // bh)
        for x in range(x0, x0 + bw):
            gx = min(GRID - 1, (x - x0) * GRID // bw)
            cell = gy * GRID + gx
            total[cell] += 1
            p = (y * width + x) * 4
            a = rgba[p + 3] / 255
            luma = 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2]
            if a <= 0.15 and luma <= 60:
                continue
            art[cell] += 1
            rgb[cell * 3] += rgba[p]
            rgb[cell * 3 + 1] += rgba[p + 1]
            rgb[cell * 3 + 2] += rgba[p + 2]
    for i in range(GRID * GRID):
        cov[i] = art[i] / total[i] if total[i] else 0.0
        n = art[i] or 1
        rgb[i * 3] /= n
        rgb[i * 3 + 1] /= n
        rgb[i * 3 + 2] /= n
    return cov, rgb


def main():
    from PIL import Image

    os.makedirs(ICONS, exist_ok=True)
    champions = json.load(open(os.path.join(DATA, "champions.json"), encoding="utf-8"))["champions"]
    version = json.loads(fetch("https://ddragon.leagueoflegends.com/api/versions.json"))[0]
    dd = json.loads(fetch(f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json"))
    by_id = {}
    for entry in dd["data"].values():
        by_id[int(entry["key"])] = entry["image"]["full"]

    items = []
    downloaded = 0
    for champ in champions:
        cid = champ["id"]
        filename = by_id.get(cid)
        if not filename:
            continue
        path = os.path.join(ICONS, f"{cid}.png")
        if not os.path.exists(path):
            data = fetch(f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{filename}")
            with open(path, "wb") as handle:
                handle.write(data)
            downloaded += 1
        image = Image.open(path).convert("RGBA")
        cov, rgb = describe_rgba(image.tobytes(), image.width, image.height)
        items.append({
            "id": cid,
            "cov": [round(v, 3) for v in cov],
            "rgb": [int(v) for v in rgb],
        })

    out = {"version": version, "grid": GRID, "items": items}
    with open(os.path.join(DATA, "champ-index.json"), "w", encoding="utf-8") as handle:
        json.dump(out, handle, ensure_ascii=False)
    print(f"[champ-index] {len(items)} portraits (downloaded {downloaded}), ddragon {version}")


if __name__ == "__main__":
    main()
