"""Genera un thumbnail JPG (preview) de un PSD usando psd-tools + Pillow.

El thumbnail se usa en la UI Export Pack para que el usuario vea qué PSD
está por exportar antes de elegir resoluciones.

Output: PNG/JPG bytes de máximo `max_side` px en el lado largo.
"""

from __future__ import annotations

import io
from pathlib import Path


def extract_psd_preview_jpg(psd_path: Path, max_side: int = 512) -> bytes | None:
    """Abre el PSD, aplana el composite y devuelve JPG bytes redimensionado.

    Retorna None si psd-tools/Pillow no están instalados o si el archivo
    no es un PSD válido.
    """
    try:
        from psd_tools import PSDImage  # type: ignore[import-untyped]
        from PIL import Image  # type: ignore[import-untyped]
    except ImportError:
        return None

    try:
        psd = PSDImage.open(str(psd_path))
        composite = psd.composite()
        if composite is None:
            return None
        if composite.mode != "RGBA":
            composite = composite.convert("RGBA")
        w, h = composite.size
        longest = max(w, h)
        if longest > max_side:
            scale = max_side / longest
            new_w = max(1, round(w * scale))
            new_h = max(1, round(h * scale))
            composite = composite.resize((new_w, new_h), Image.LANCZOS)
        # Aplanar sobre fondo blanco para JPG
        bg = Image.new("RGB", composite.size, (255, 255, 255))
        if composite.mode == "RGBA":
            bg.paste(composite, mask=composite.split()[3])
        else:
            bg.paste(composite)
        buf = io.BytesIO()
        bg.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


def get_psd_dimensions(psd_path: Path) -> tuple[int, int] | None:
    """Devuelve (width, height) del PSD sin generar imagen (rápido)."""
    try:
        from psd_tools import PSDImage  # type: ignore[import-untyped]
    except ImportError:
        return None
    try:
        psd = PSDImage.open(str(psd_path))
        return (int(psd.width or 0), int(psd.height or 0))
    except Exception:  # noqa: BLE001
        return None
