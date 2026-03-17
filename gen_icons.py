#!/usr/bin/env python3
"""Generate PWA icons for Health Guard portal — shield + cross logo."""
import os
import math

try:
    from PIL import Image, ImageDraw
except ImportError:
    raise SystemExit("Pillow required: pip install Pillow")


def bezier_pts(p0, p1, p2, steps=14):
    """Quadratic Bezier approximated as line segments (normalized 0-1 coords)."""
    pts = []
    for i in range(steps + 1):
        t = i / steps
        x = (1 - t)**2 * p0[0] + 2 * (1 - t) * t * p1[0] + t**2 * p2[0]
        y = (1 - t)**2 * p0[1] + 2 * (1 - t) * t * p1[1] + t**2 * p2[1]
        pts.append((x, y))
    return pts


def shield_polygon(size):
    """Return list of (x, y) pixel coords for a heraldic shield shape."""
    def scale(pts):
        return [(x * size, y * size) for x, y in pts]

    pts = []
    # Top-left rounded corner
    pts += bezier_pts((0.18, 0.063), (0.095, 0.063), (0.095, 0.133), steps=7)
    # Left side straight
    pts.append((0.095, 0.590))
    # Bottom-left curve to point
    pts += bezier_pts((0.095, 0.590), (0.095, 0.840), (0.500, 0.963), steps=18)
    # Bottom-right curve from point
    pts += bezier_pts((0.500, 0.963), (0.905, 0.840), (0.905, 0.590), steps=18)
    # Right side straight
    pts.append((0.905, 0.133))
    # Top-right rounded corner
    pts += bezier_pts((0.905, 0.133), (0.905, 0.063), (0.820, 0.063), steps=7)

    return scale(pts)


def make_icon(size):
    img = Image.new('RGB', (size, size), (8, 15, 29))   # dark navy bg
    draw = ImageDraw.Draw(img)

    poly = shield_polygon(size)

    # ── Shield fill: vertical gradient (light green → dark green) ──────────
    # Draw horizontal scan lines over the bounding box with interpolated color
    top_col  = (30, 160, 82)   # #1ea052
    bot_col  = (18,  83, 45)   # #12532d
    bbox_top    = min(y for _, y in poly)
    bbox_bottom = max(y for _, y in poly)
    span = max(1, bbox_bottom - bbox_top)

    # Create a mask by drawing the polygon, then colorize row by row
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)

    # Build gradient layer
    grad = Image.new('RGB', (size, size), (0, 0, 0))
    grad_draw = ImageDraw.Draw(grad)
    for y in range(size):
        t = max(0.0, min(1.0, (y - bbox_top) / span))
        r = int(top_col[0] + (bot_col[0] - top_col[0]) * t)
        g = int(top_col[1] + (bot_col[1] - top_col[1]) * t)
        b = int(top_col[2] + (bot_col[2] - top_col[2]) * t)
        grad_draw.line([(0, y), (size, y)], fill=(r, g, b))

    img.paste(grad, mask=mask)

    # ── Subtle top highlight ────────────────────────────────────────────────
    hl_poly = []
    hl_poly += bezier_pts((0.18, 0.063), (0.095, 0.063), (0.095, 0.133), steps=7)
    hl_poly.append((0.095, 0.430))
    hl_poly += bezier_pts((0.095, 0.430), (0.300, 0.390), (0.500, 0.385), steps=8)
    hl_poly += bezier_pts((0.500, 0.385), (0.700, 0.390), (0.905, 0.430), steps=8)
    hl_poly.append((0.905, 0.133))
    hl_poly += bezier_pts((0.905, 0.133), (0.905, 0.063), (0.820, 0.063), steps=7)
    hl_poly_px = [(x * size, y * size) for x, y in hl_poly]

    hl = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(hl).polygon(hl_poly_px, fill=(255, 255, 255, 22))
    img.paste(Image.new('RGB', (size, size), (255, 255, 255)),
              mask=hl.split()[3])

    # ── Shield border ───────────────────────────────────────────────────────
    border_lw = max(2, int(size * 0.014))
    draw.line(poly + [poly[0]], fill=(255, 255, 255, 50), width=border_lw)

    # ── White cross ─────────────────────────────────────────────────────────
    cx = size * 0.500
    cy = size * 0.500      # cross center (vertically centered in shield body)
    aw = size * 0.120      # arm width
    al = size * 0.185      # arm half-length
    r  = max(2, int(size * 0.018))  # corner radius (approximate with slight inset)

    vx0, vy0 = cx - aw / 2, cy - al
    vx1, vy1 = cx + aw / 2, cy + al
    hx0, hy0 = cx - al,     cy - aw / 2
    hx1, hy1 = cx + al,     cy + aw / 2

    draw.rectangle([vx0, vy0, vx1, vy1], fill=(255, 255, 255))
    draw.rectangle([hx0, hy0, hx1, hy1], fill=(255, 255, 255))

    return img


def make_splash(width, height):
    """Splash screen: dark navy background with centered shield icon."""
    img = Image.new('RGB', (width, height), (8, 15, 29))
    icon_size = min(width, height) // 4
    icon = make_icon(icon_size)
    ox = (width  - icon_size) // 2
    oy = (height - icon_size) // 2
    img.paste(icon, (ox, oy))
    return img


def main():
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "app", "portal", "static", "portal", "icons")
    os.makedirs(out_dir, exist_ok=True)

    # PWA icons
    for size, name in [(512, "icon-512.png"), (192, "icon-192.png"), (180, "apple-touch-icon.png")]:
        icon = make_icon(size)
        path = os.path.join(out_dir, name)
        icon.save(path, "PNG", optimize=True)
        print(f"  ✓ {name}  ({size}×{size})")

    # Splash screens
    for (w, h), name in [
        ((1290, 2796), "splash-1290x2796.png"),
        ((1179, 2556), "splash-1179x2556.png"),
        ((1170, 2532), "splash-1170x2532.png"),
        ((1125, 2436), "splash-1125x2436.png"),
        (( 828, 1792), "splash-828x1792.png"),
        (( 750, 1334), "splash-750x1334.png"),
    ]:
        splash = make_splash(w, h)
        path = os.path.join(out_dir, name)
        splash.save(path, "PNG", optimize=True)
        print(f"  ✓ {name}  ({w}×{h})")


if __name__ == "__main__":
    main()
