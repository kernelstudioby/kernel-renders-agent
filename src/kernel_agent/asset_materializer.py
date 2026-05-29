"""Pre-procesa el plan antes de ejecutar: descarga URLs de assets a tempfiles.

El server puede mandar assets como URL pública (Supabase Storage); el script
de Blender necesita rutas locales. Esta función recorre el plan, identifica
campos que se ven como URL HTTPS, los descarga a una carpeta temporal y
reescribe los argumentos del plan con la ruta local.

Solo materializa campos conocidos para no descargar cualquier string que
parezca URL — únicamente: png_path, label_path, texture_path, asset_path.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import httpx

URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Campos del plan que pueden contener una URL → descargar a disco
DOWNLOADABLE_KEYS = {
    "png_path",
    "label_path",
    "texture_path",
    "asset_path",
}


def materialize_plan_assets(plan: list[dict], cache_dir: Path) -> list[dict]:
    """Devuelve un plan con URLs reemplazadas por rutas locales.

    Modifica una copia — el plan original queda intacto.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for step in plan:
        new_args = dict(step.get("args") or {})
        for key in list(new_args.keys()):
            if key not in DOWNLOADABLE_KEYS:
                continue
            val = new_args[key]
            if not isinstance(val, str) or not URL_RE.match(val):
                continue
            new_args[key] = _download_to_cache(val, cache_dir)
        out.append({**step, "args": new_args})
    return out


def _download_to_cache(url: str, cache_dir: Path) -> str:
    """Descarga la URL a cache_dir con un nombre determinístico. Idempotente."""
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    ext = _guess_ext(url)
    local = cache_dir / f"{h}{ext}"
    if local.exists() and local.stat().st_size > 0:
        return str(local)
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"download {url} failed: HTTP {r.status_code}")
        tmp = local.with_suffix(local.suffix + ".part")
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
        os.replace(tmp, local)
    return str(local)


def _guess_ext(url: str) -> str:
    """Adivina la extensión a partir del path del URL. Default .bin."""
    # Quita query y fragment
    path = url.split("?", 1)[0].split("#", 1)[0]
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".bin"):
        if path.lower().endswith(ext):
            return ext
    return ".bin"
