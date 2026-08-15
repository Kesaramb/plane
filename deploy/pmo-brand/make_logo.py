#!/usr/bin/env python3
"""Generate the PMO brand assets from geometry, so every surface stays in sync.

The mark is "PM" followed by a gear whose hole reads as the O. Everything is
derived from one cap height and one brand blue, so changing either re-renders
the whole set consistently.

Run:  python3 make_logo.py
Out:  pmo-logo.png (email lockup), pmo-mark.png (wordmark), gradient-logo.webp
      (SPA auth logo), favicon-16/32.png, favicon.ico, icon-192/512.png

Rendered at 4x and downsampled, which is how the edges stay clean without any
SVG rasteriser on the box. If the real vector logo ever turns up, drop the
PNGs in beside these names and nothing else has to change.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))

BLUE = (42, 169, 224, 255)  # --blue from the BrandFactory landing page
INK = (17, 17, 17, 255)
WHITE = (255, 255, 255, 255)
SS = 4  # supersample factor

BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
REG = "/System/Library/Fonts/Supplemental/Arial.ttf"


def gear_points(cx, cy, r_out, teeth=8, root=0.80, tooth=0.24, flank=0.31):
    """Outline of a gear: flat-topped teeth joined by arcs at the root radius."""
    pitch = 2 * math.pi / teeth
    r_root = r_out * root
    pts = []
    for i in range(teeth):
        base = i * pitch - math.pi / 2
        a_top, a_root = pitch * tooth, pitch * flank
        # up the leading flank, across the tooth, down the trailing flank
        for ang, rad in (
            (base - a_root, r_root),
            (base - a_top, r_out),
            (base + a_top, r_out),
            (base + a_root, r_root),
        ):
            pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
        # arc along the root out to the next tooth, sampled so it reads as round
        nxt = base + pitch - a_root
        steps = 6
        for s in range(1, steps):
            ang = base + a_root + (nxt - (base + a_root)) * s / steps
            pts.append((cx + r_root * math.cos(ang), cy + r_root * math.sin(ang)))
    return pts


def draw_gear(draw, cx, cy, r_out, colour, hole=0.42):
    draw.polygon(gear_points(cx, cy, r_out), fill=colour)
    r_h = r_out * hole
    draw.ellipse([cx - r_h, cy - r_h, cx + r_h, cy + r_h], fill=(0, 0, 0, 0))


def text_size(font, s):
    box = font.getbbox(s)
    return box[2] - box[0], box[3] - box[1], box[0], box[1]


def render_mark(cap, ink=INK, blue=BLUE, pad_ratio=0.14):
    """"PM" + gear, transparent background. cap = cap height in final pixels."""
    c = cap * SS
    font = ImageFont.truetype(BOLD, int(c * 1.36))  # cap height ~= 0.72 of em
    tw, th, ox, oy = text_size(font, "PM")

    gear_r = c * 0.62           # gear stands a little proud of the caps
    gap = c * 0.06
    pad = int(c * pad_ratio)

    w = pad + tw + gap + gear_r * 2 + pad
    h = pad + max(th, gear_r * 2) + pad

    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    mid = h / 2
    d.text((pad - ox, mid - th / 2 - oy), "PM", font=font, fill=ink)
    draw_gear(d, pad + tw + gap + gear_r, mid, gear_r, blue)

    return img.resize((int(w / SS), int(h / SS)), Image.LANCZOS)


def render_lockup(cap, ink=INK, blue=BLUE):
    """Full lockup: the mark with the tagline set beneath it, tracked to width."""
    mark = render_mark(cap, ink, blue)
    tag = "Project Management Office - BrandFactory"

    # size the tagline so it spans the mark's width: measure once, then scale
    ref = 100 * SS
    ref_w, _, _, _ = text_size(ImageFont.truetype(REG, ref), tag)
    size = int(ref * (mark.width * SS * 0.98) / ref_w)

    f = ImageFont.truetype(REG, size)
    tw, th, ox, oy = text_size(f, tag)
    gap = int(cap * 0.16)

    strip = Image.new("RGBA", (int(tw + 4), int(th + 4)), (0, 0, 0, 0))
    ImageDraw.Draw(strip).text((-ox, -oy), tag, font=f, fill=ink)
    strip = strip.resize((max(1, strip.width // SS), max(1, strip.height // SS)), Image.LANCZOS)

    w = max(mark.width, strip.width)
    out = Image.new("RGBA", (w, mark.height + gap + strip.height), (0, 0, 0, 0))
    out.paste(mark, ((w - mark.width) // 2, 0), mark)
    out.paste(strip, ((w - strip.width) // 2, mark.height + gap), strip)
    return out


def render_icon(px, blue=BLUE):
    """Square gear icon — the one element that still reads at 16 pixels.

    Below 48px the eight teeth collapse into a blur, so small sizes get six
    chunkier ones. A favicon is how people find the tab; legible beats faithful.
    """
    s = px * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if px < 48:
        d.polygon(gear_points(s / 2, s / 2, s * 0.48, teeth=6, root=0.72, tooth=0.22, flank=0.30), fill=blue)
        r_h = s * 0.48 * 0.38
        d.ellipse([s / 2 - r_h, s / 2 - r_h, s / 2 + r_h, s / 2 + r_h], fill=(0, 0, 0, 0))
    else:
        draw_gear(d, s / 2, s / 2, s * 0.48, blue, hole=0.40)
    return img.resize((px, px), Image.LANCZOS)


def save(img, name, **kw):
    path = os.path.join(HERE, name)
    img.save(path, **kw)
    print(f"  {name:28} {img.width}x{img.height}")


if __name__ == "__main__":
    print("PMO brand assets ->", HERE)

    save(render_lockup(120), "pmo-logo.png")          # light backgrounds
    save(render_lockup(120, ink=WHITE), "pmo-logo-white.png")  # the black email header
    save(render_mark(90), "pmo-mark.png")             # wordmark, dark ink
    save(render_mark(90, ink=WHITE), "pmo-mark-white.png")

    # SPA auth logo: same slot as Plane's gradient-logo.webp, so keep it square-ish
    save(render_icon(512), "gradient-logo.webp", format="WEBP", lossless=True)

    for px in (16, 32):
        save(render_icon(px), f"favicon-{px}.png")
    for px in (180, 192, 512):
        save(render_icon(px), f"icon-{px}.png")

    # og:image — the lockup centred on white, at the size link previews expect
    og = Image.new("RGBA", (1200, 630), (255, 255, 255, 255))
    lock = render_lockup(190)
    og.paste(lock, ((1200 - lock.width) // 2, (630 - lock.height) // 2), lock)
    save(og.convert("RGB"), "og-image.png")

    ico = render_icon(64)
    ico.save(os.path.join(HERE, "favicon.ico"),
             sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print("  favicon.ico                  multi-size")
