"""Extrae el thumbnail embebido (128x128) de un .blend de Blender 5.x.

Formato (Blender 5.x con large-block header de 17 bytes):
  Header (17 bytes):
    [0..6]   'BLENDER'
    [7..8]   '17'  (header size as ASCII)
    [9]      '-' (pointer 8) o '_' (pointer 4)
    [10..11] file format version
    [12]     'v' (little endian) o 'V' (big)
    [13..15] blender version (e.g. '050' = 5.0)
    [16]     '1' (large header flag)

  Block header (32 bytes con large-header flag):
    [0..3]   code (4 chars, e.g. 'TEST')
    [4..7]   padding
    [8..15]  old memory address (uint64)
    [16..23] size de los datos (uint64)
    [24..27] sdna index
    [28..31] count

  Bloque TEST (thumbnail):
    width (int32) + height (int32) + BGRA pixels (width*height*4 bytes, bottom-up)

Si el archivo no tiene "Save Thumbnail" habilitado, no hay bloque TEST y
retornamos None.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path


_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def extract_blend_thumbnail_png(blend_path: Path) -> bytes | None:
    """Devuelve PNG bytes del thumbnail embebido, o None si no hay."""
    try:
        with open(blend_path, "rb") as f:
            magic = f.read(7)
            if magic != b"BLENDER":
                return None
            # En Blender 5.x el header es 17 bytes con large-block flag.
            # Leemos los 10 bytes restantes del header para extraer el resto
            # de la metadata.
            rest = f.read(10)
            if len(rest) < 10:
                return None
            # rest[0:2] = '17' (header size); rest[2] = '-' o '_'
            # rest[5] = 'v' o 'V' (endian); rest[9] = '1' (large flag)
            endian = "<" if rest[5:6] == b"v" else ">"
            block_header_size = 32  # large-block header en Blender 5.x

            # Iterar bloques hasta encontrar TEST o ENDB.
            # Solo escaneamos los primeros ~10MB; el TEST en escenas de Moy
            # aparece en los primeros KB (offset 313 típicamente).
            max_scan = 20 * 1024 * 1024
            scanned = 0
            while scanned < max_scan:
                bh = f.read(block_header_size)
                if len(bh) < block_header_size:
                    return None
                scanned += block_header_size
                code = bh[0:4].rstrip(b"\x00")
                # size es uint64 little-endian en offset 16
                size = struct.unpack(endian + "Q", bh[16:24])[0]
                if code == b"ENDB":
                    return None
                if code == b"TEST":
                    return _read_thumb_data(f, size, endian)
                # Skip data
                f.seek(size, 1)
                scanned += size
    except OSError:
        return None
    return None


def _read_thumb_data(f, size: int, endian: str) -> bytes | None:
    wh = f.read(8)
    if len(wh) < 8:
        return None
    w, h = struct.unpack(endian + "ii", wh)
    if w <= 0 or h <= 0 or w > 1024 or h > 1024:
        return None
    px_len = w * h * 4
    pixels = f.read(px_len)
    if len(pixels) < px_len:
        return None
    # Blender guarda BGRA bottom-up. Convertimos a RGBA top-down y empaquetamos PNG.
    return _bgra_to_png(w, h, pixels)


def _bgra_to_png(w: int, h: int, bgra: bytes) -> bytes:
    """Convierte raw BGRA bottom-up a PNG RGBA top-down (sin Pillow)."""
    row = w * 4
    flipped = bytearray(len(bgra))
    for y in range(h):
        src = (h - 1 - y) * row
        dst = y * row
        flipped[dst : dst + row] = bgra[src : src + row]
    # BGRA -> RGBA in place
    rgba = bytearray(len(flipped))
    for i in range(0, len(flipped), 4):
        rgba[i] = flipped[i + 2]
        rgba[i + 1] = flipped[i + 1]
        rgba[i + 2] = flipped[i]
        rgba[i + 3] = flipped[i + 3]

    out = io.BytesIO()
    out.write(_PNG_SIG)

    def _chunk(tag: bytes, data: bytes) -> None:
        out.write(struct.pack(">I", len(data)))
        out.write(tag)
        out.write(data)
        crc = zlib.crc32(tag)
        crc = zlib.crc32(data, crc)
        out.write(struct.pack(">I", crc & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    _chunk(b"IHDR", ihdr)

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw.extend(rgba[y * row : (y + 1) * row])
    _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    _chunk(b"IEND", b"")

    return out.getvalue()
