"""Soraka avatar frames for the pill.

The pill shows Soraka's official square portrait, cropped to a circle. A blink is
faked by lowering a lid over each eye: the cheek just below the eye is cloned over
the eye with a feathered mask, and a thin lash line marks the lid edge. Frames are
cached under data/avatar and rebuilt when the source icon changes.
"""

import os

from PIL import Image, ImageDraw, ImageFilter

SIZE = 64

# Eye boxes in fractions of the portrait (measured on the 64 px frame).
EYE_BOXES = ((0.15, 0.45, 0.34, 0.61), (0.46, 0.29, 0.67, 0.45))


def _eye_patch(image, box):
    """A same-size patch of cheek taken from just below the eye."""
    width, height = image.size
    x0, y0, x1, y1 = (int(box[0] * width), int(box[1] * height), int(box[2] * width), int(box[3] * height))
    box_h = max(3, y1 - y0)
    src_top = min(max(0, height - box_h - 1), y1 + 1)
    return image.crop((x0, src_top, x1, src_top + box_h)), (x0, y0, x1, y1)


def _eye_mask(image):
    """Mask of the two glowing eyes, restricted to the face region.

    The portrait's pink hair is also warm and bright, so the search is clamped to
    the band where the eyes actually sit; otherwise the crease line would be drawn
    across the top of the head.
    """
    px = image.load()
    width, height = image.size
    mask = Image.new("L", (width, height), 0)
    mpx = mask.load()
    for y in range(int(height * 0.24), int(height * 0.64)):
        for x in range(int(width * 0.02), int(width * 0.80)):
            r, g, b = px[x, y]
            if r > 120 and r - b > 40 and g > 50 and g < 185:
                mpx[x, y] = 255
    return mask.filter(ImageFilter.MaxFilter(5))


def _apply_lid(image, strength):
    """Fill the eyes with neighbouring skin and add a lid crease above them."""
    mask = _eye_mask(image)
    if mask.getbbox() is None:
        return image

    skin = image.filter(ImageFilter.GaussianBlur(3.5))
    feathered = mask.filter(ImageFilter.GaussianBlur(1.2))
    if strength >= 0.99:
        image.paste(skin, (0, 0), feathered)
    else:
        blended = Image.composite(skin, image, feathered)
        image.paste(Image.blend(image, blended, strength), (0, 0))

    # A soft shadow hints at the lid without drawing lines over the artwork.
    shade = Image.new("RGBA", image.size, (0, 0, 0, 0))
    dark = Image.new("RGBA", image.size, (40, 18, 54, 90))
    shade = Image.composite(dark, shade, feathered)
    shade = shade.filter(ImageFilter.GaussianBlur(1.5))
    image.paste(Image.alpha_composite(image.convert("RGBA"), shade).convert("RGB"), (0, 0))
    return image


def build_frames(icon_path, size=SIZE):
    """Return {"open": Image, "half": Image, "closed": Image} in RGB."""
    source = Image.open(icon_path).convert("RGB")
    # DDragon portraits carry a dark frame baked into the artwork; crop it away so
    # the avatar fills the circle instead of showing a black square.
    inset = int(min(source.size) * 0.07)
    source = source.crop((inset, inset, source.width - inset, source.height - inset))
    open_frame = source.resize((size, size), Image.LANCZOS)

    half = _apply_lid(open_frame.copy(), 0.55)
    closed = _apply_lid(open_frame.copy(), 1.0)

    return {"open": open_frame, "half": half, "closed": closed}


def cache_dir(data_dir):
    return os.path.join(data_dir, "avatar")


def ensure_frames(icon_path, data_dir, size=SIZE):
    """Build (or reuse) cached frames; returns {state: path}."""
    target_dir = cache_dir(data_dir)
    os.makedirs(target_dir, exist_ok=True)
    stamp = int(os.path.getmtime(icon_path))

    paths = {}
    missing = False
    for state in ("open", "half", "closed"):
        path = os.path.join(target_dir, f"soraka-{size}-{state}.png")
        paths[state] = path
        if not os.path.exists(path) or os.path.getmtime(path) < stamp:
            missing = True
    if not missing:
        return paths

    frames = build_frames(icon_path, size)
    for state, image in frames.items():
        image.save(paths[state])
    return paths


def main():
    import argparse

    parser = argparse.ArgumentParser(description="build the Soraka pill frames")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("--icon", default=os.path.join(base, "data", "champ-icons", "16.png"))
    parser.add_argument("--data", default=os.path.join(base, "data"))
    parser.add_argument("--size", type=int, default=SIZE)
    args = parser.parse_args()

    paths = ensure_frames(args.icon, args.data, args.size)
    for state, path in paths.items():
        print(state, path, os.path.getsize(path))


if __name__ == "__main__":
    main()
