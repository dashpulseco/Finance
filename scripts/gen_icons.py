"""Generate the committed PWA icons under site/icons/ with Pillow.

Run once, by path, from the Finan folder:

    py -3 scripts\\gen_icons.py

No fonts are used: there is nothing to break across machines and nothing that stops being legible
at 180 px. The artwork is a rounded-rectangle dark plate with a rising four-point polyline and a
filled arrowhead, so every output is a pure function of its pixel size - re-running the script
rewrites the same bytes and produces no git diff.
"""
import sys
from pathlib import Path

# Make `import finan` work when this file is run by path (py -3 scripts/gen_icons.py) from any
# cwd: running a script puts its own folder on sys.path, not the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw                                    # noqa: E402
from finan.settings import BASE_DIR, SITE_DIR                       # noqa: E402

BG = (15, 20, 26, 255)          # #0f141a, the app's --bg token
FG = (47, 191, 113, 255)        # #2fbf71, the app's --level-none token
CORNER_FRAC = 0.22              # rounded-rectangle radius, as a fraction of the size
STROKE_FRAC = 0.085             # polyline width, as a fraction of the size (design-fixed)
HEAD_LEN = 1.6                  # arrowhead length, in stroke widths
HEAD_HALF = 1.0                 # arrowhead half-base, in stroke widths

# The four polyline points, rising left to right, in 0..1 artwork-box coordinates (y grows down).
POLYLINE = ((0.00, 0.88), (0.34, 0.60), (0.64, 0.47), (1.00, 0.08))


def _artwork_inset(size: int, margin_frac: float, stroke: float) -> float:
    """Distance from the image edge to the artwork box.

    The box holds the polyline *points*; the stroke adds half a width around them and the
    arrowhead reaches at most hypot(HEAD_LEN / 2, HEAD_HALF) widths past the last point. Both are
    added here so nothing the drawing emits can cross `margin_frac` of the edge - which is what
    keeps the maskable variant inside its central 80 % safe zone.
    """
    overhang = max(0.5, ((HEAD_LEN / 2) ** 2 + HEAD_HALF ** 2) ** 0.5)
    return margin_frac * size + overhang * stroke


def render(size: int, margin_frac: float, full_bleed: bool) -> Image.Image:
    """Draw one icon. `full_bleed` paints a square opaque plate (iOS applies its own mask)."""
    stroke = round(STROKE_FRAC * size)
    if full_bleed:
        image = Image.new("RGBA", (size, size), BG)
        draw = ImageDraw.Draw(image)
    else:
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, size - 1, size - 1),
                               radius=round(CORNER_FRAC * size), fill=BG)

    inset = _artwork_inset(size, margin_frac, stroke)
    side = size - 2 * inset
    points = [(inset + px * side, inset + py * side) for px, py in POLYLINE]
    draw.line(points, fill=FG, width=stroke, joint="curve")

    # Filled triangle arrowhead, aligned with the last segment.
    (x3, y3), (x4, y4) = points[-2], points[-1]
    dx, dy = x4 - x3, y4 - y3
    length = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    tip = (x4 + ux * stroke * HEAD_LEN / 2, y4 + uy * stroke * HEAD_LEN / 2)
    base = (x4 - ux * stroke * HEAD_LEN / 2, y4 - uy * stroke * HEAD_LEN / 2)
    draw.polygon((tip,
                  (base[0] + nx * stroke * HEAD_HALF, base[1] + ny * stroke * HEAD_HALF),
                  (base[0] - nx * stroke * HEAD_HALF, base[1] - ny * stroke * HEAD_HALF)),
                 fill=FG)
    return image


# name -> (size, margin from the edge, full-bleed opaque square)
ICONS = {
    "icon-192.png": (192, 0.08, False),
    "icon-512.png": (512, 0.08, False),
    # purpose: "maskable" - every drawn pixel stays inside the central 80 % safe zone.
    "icon-512-maskable.png": (512, 0.10, False),
    # iOS applies its own corner mask and does not honour transparency, so this one is opaque.
    "apple-touch-icon.png": (180, 0.08, True),
}


def main() -> int:
    out_dir = SITE_DIR / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, (size, margin_frac, full_bleed) in ICONS.items():
        path = out_dir / name
        render(size, margin_frac, full_bleed).save(path, format="PNG", optimize=True)
        # Relative, because this line gets pasted into public issues.
        print(f"wrote {path.relative_to(BASE_DIR)} ({size}x{size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
