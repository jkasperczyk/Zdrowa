#!/usr/bin/env python3
"""Generate PWA icons for Zdrowa portal."""
import struct, zlib, math, os

# ── PNG helpers ──────────────────────────────────────────────────────────────

def _chunk(tag: bytes, data: bytes) -> bytes:
    c = struct.pack('>I', len(data)) + tag + data
    return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

def make_png(width: int, height: int, rows: list) -> bytes:
    """rows: list of (width * [(r,g,b,a), ...])"""
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for r, g, b, a in row:
            raw += bytes([r, g, b, a])
    idat = _chunk(b'IDAT', zlib.compress(bytes(raw), 9))
    iend = _chunk(b'IEND', b'')
    return sig + ihdr + idat + iend

# ── Drawing helpers ───────────────────────────────────────────────────────────

BG    = (8,   15,  29,  255)   # #080f1d  dark navy
BLUE  = (79,  143, 255, 255)   # #4f8fff  accent
WHITE = (255, 255, 255, 255)

def lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i]-c1[i])*t) for i in range(4))

def make_icon(size: int) -> bytes:
    px = [BG] * (size * size)

    cx = cy = size / 2.0
    outer_r = size * 0.42
    inner_r  = size * 0.30

    # ── Radial gradient circle (dark→blue) ────────────────────────────────
    for y in range(size):
        for x in range(size):
            dx, dy = x - cx, y - cy
            dist = math.sqrt(dx*dx + dy*dy)
            if dist <= outer_r:
                t = max(0.0, 1.0 - dist / outer_r)
                col = lerp_color((30, 50, 100, 255), BLUE, t * 0.85)
                # soft anti-alias at edge
                if dist > outer_r - 2:
                    a = int(255 * (outer_r - dist) / 2)
                    col = (col[0], col[1], col[2], a)
                px[y*size + x] = col

    # ── Bold "Z" letter ────────────────────────────────────────────────────
    def set_rect(x0, y0, x1, y1, color=WHITE):
        for yy in range(max(0,y0), min(size,y1)):
            for xx in range(max(0,x0), min(size,x1)):
                px[yy*size + xx] = color

    # Z proportions relative to size
    zw    = int(size * 0.32)   # total width
    zh    = int(size * 0.34)   # total height
    thick = max(3, int(size * 0.065))  # bar thickness

    lx = int(cx) - zw//2
    rx = lx + zw
    ty = int(cy) - zh//2
    by = ty + zh

    # top bar
    set_rect(lx, ty, rx, ty + thick)
    # bottom bar
    set_rect(lx, by - thick, rx, by)

    # diagonal: staircase from top-right to bottom-left
    diag_h = zh - 2*thick
    steps  = max(6, zw // thick)
    for i in range(steps):
        seg_y0 = ty + thick + int(i     * diag_h / steps)
        seg_y1 = ty + thick + int((i+1) * diag_h / steps) + 1
        seg_x1 = rx - int(i       * zw / steps)
        seg_x0 = rx - int((i+1)   * zw / steps) - thick
        set_rect(seg_x0, seg_y0, seg_x1, seg_y1)

    rows = [px[y*size:(y+1)*size] for y in range(size)]
    return make_png(size, size, rows)

# ── Main ─────────────────────────────────────────────────────────────────────

def make_splash(width: int, height: int) -> bytes:
    """Splash screen: dark background with centered icon."""
    BG = (8, 15, 29, 255)
    px = [BG] * (width * height)

    # Draw a centered scaled icon in the middle
    icon_size = min(width, height) // 4
    ox = (width  - icon_size) // 2
    oy = (height - icon_size) // 2

    cx = width  / 2.0
    cy = height / 2.0
    outer_r = icon_size * 0.42

    for y in range(oy, oy + icon_size):
        for x in range(ox, ox + icon_size):
            dx, dy = x - cx, y - cy
            dist = math.sqrt(dx*dx + dy*dy)
            if dist <= outer_r:
                t = max(0.0, 1.0 - dist / outer_r)
                col = lerp_color((30, 50, 100, 255), BLUE, t * 0.85)
                if dist > outer_r - 2:
                    a = int(255 * (outer_r - dist) / 2)
                    col = (col[0], col[1], col[2], a)
                px[y*width + x] = col

    # Z letter scaled to icon area
    zw    = int(icon_size * 0.32)
    zh    = int(icon_size * 0.34)
    thick = max(2, int(icon_size * 0.065))
    lx = int(cx) - zw//2
    rx = lx + zw
    ty = int(cy) - zh//2
    by = ty + zh

    def set_rect(x0, y0, x1, y1):
        for yy in range(max(0,y0), min(height,y1)):
            for xx in range(max(0,x0), min(width,x1)):
                px[yy*width + xx] = WHITE

    set_rect(lx, ty, rx, ty+thick)
    set_rect(lx, by-thick, rx, by)
    diag_h = zh - 2*thick
    steps  = max(6, zw // thick)
    for i in range(steps):
        seg_y0 = ty + thick + int(i     * diag_h / steps)
        seg_y1 = ty + thick + int((i+1) * diag_h / steps) + 1
        seg_x1 = rx - int(i       * zw / steps)
        seg_x0 = rx - int((i+1)   * zw / steps) - thick
        set_rect(seg_x0, seg_y0, seg_x1, seg_y1)

    rows = [px[y*width:(y+1)*width] for y in range(height)]
    return make_png(width, height, rows)


def main():
    out_dir = os.path.join(os.path.dirname(__file__),
                           "app", "portal", "static", "portal", "icons")
    os.makedirs(out_dir, exist_ok=True)

    for size, name in [(192, "icon-192.png"), (512, "icon-512.png"), (180, "apple-touch-icon.png")]:
        data = make_icon(size)
        path = os.path.join(out_dir, name)
        with open(path, "wb") as f:
            f.write(data)
        print(f"  ✓ {name}  ({size}×{size}, {len(data):,} bytes)")

    # Splash screens (use small size to keep files reasonable — iOS scales up)
    for (w, h), name in [
        ((1290, 2796), "splash-1290x2796.png"),
        ((1179, 2556), "splash-1179x2556.png"),
        ((1170, 2532), "splash-1170x2532.png"),
        ((1125, 2436), "splash-1125x2436.png"),
        (( 828, 1792), "splash-828x1792.png"),
        (( 750, 1334), "splash-750x1334.png"),
    ]:
        data = make_splash(w, h)
        path = os.path.join(out_dir, name)
        with open(path, "wb") as f:
            f.write(data)
        print(f"  ✓ {name}  ({w}×{h}, {len(data):,} bytes)")

if __name__ == "__main__":
    main()
