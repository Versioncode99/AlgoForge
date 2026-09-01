"""Render the AlgoForge mark to a multi-resolution Windows .ico.

Pure standard library: the mark is four rounded bars narrowing to a lit survivor,
so it can be drawn directly rather than rasterised from the SVG. Supersampled 4x
for clean edges at 16px, where the shortcut icon actually lives.

    python scripts/make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SS = 4  # supersampling factor
SIZES = (16, 24, 32, 48, 64, 128, 256)

BG = (0x0A, 0x0B, 0x0D, 255)
STEEL = (0x9A, 0xA3, 0xAD)
BRAND = (0x3D, 0xDC, 0x97)

# (x, y, w, h, rgb, alpha) in the SVG's 32-unit coordinate space
BARS = [
    (6.0, 7.0, 20.0, 2.8, STEEL, 0.95),
    (8.2, 12.6, 15.6, 2.8, STEEL, 0.70),
    (10.4, 18.2, 11.2, 2.8, STEEL, 0.48),
    (12.6, 23.8, 6.8, 2.8, BRAND, 1.00),
]
BAR_RADIUS = 1.4
BG_RADIUS = 7.0


def _in_rounded_rect(
    px: float, py: float, x: float, y: float, w: float, h: float, r: float
) -> bool:
    if not (x <= px <= x + w and y <= py <= y + h):
        return False
    r = min(r, w / 2, h / 2)
    cx = min(max(px, x + r), x + w - r)
    cy = min(max(py, y + r), y + h - r)
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def render(size: int) -> bytes:
    """Return raw RGBA bytes for one square icon face."""
    hi = size * SS
    scale = 32.0 / hi
    rows: list[bytearray] = []

    for py in range(hi):
        row = bytearray()
        uy = (py + 0.5) * scale
        for px in range(hi):
            ux = (px + 0.5) * scale
            if not _in_rounded_rect(ux, uy, 0, 0, 32, 32, BG_RADIUS):
                row += b"\x00\x00\x00\x00"
                continue
            r, g, b, a = BG
            for bx, by, bw, bh, rgb, alpha in BARS:
                if _in_rounded_rect(ux, uy, bx, by, bw, bh, BAR_RADIUS):
                    r = round(r + (rgb[0] - r) * alpha)
                    g = round(g + (rgb[1] - g) * alpha)
                    b = round(b + (rgb[2] - b) * alpha)
            row += bytes((r, g, b, a))
        rows.append(row)

    # Box-downsample the supersampled buffer.
    out = bytearray()
    for y in range(size):
        for x in range(size):
            acc = [0, 0, 0, 0]
            for dy in range(SS):
                row = rows[y * SS + dy]
                base = (x * SS) * 4
                for dx in range(SS):
                    off = base + dx * 4
                    acc[0] += row[off]
                    acc[1] += row[off + 1]
                    acc[2] += row[off + 2]
                    acc[3] += row[off + 3]
            n = SS * SS
            out += bytes((acc[0] // n, acc[1] // n, acc[2] // n, acc[3] // n))
    return bytes(out)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def to_png(rgba: bytes, size: int) -> bytes:
    stride = size * 4
    raw = b"".join(b"\x00" + rgba[y * stride : (y + 1) * stride] for y in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def build_ico(path: Path) -> None:
    faces = [(size, to_png(render(size), size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(faces))
    offset = 6 + 16 * len(faces)
    entries, blobs = b"", b""
    for size, png in faces:
        entries += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0,
            0,
            1,
            32,
            len(png),
            offset,
        )
        blobs += png
        offset += len(png)
    path.write_bytes(header + entries + blobs)


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "apps" / "web" / "public" / "algoforge.ico"
    build_ico(target)
    print(f"wrote {target} ({target.stat().st_size:,} bytes, {len(SIZES)} sizes)")
