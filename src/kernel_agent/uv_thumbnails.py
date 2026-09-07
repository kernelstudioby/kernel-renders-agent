"""Miniaturas de productos UV para el picker de Kernel Web (KER3-34).

Genera un PNG pequeño por vista usando la textura generica del UV Mapper
(`Productos/generic.png`) sobre el estado neutro, y lo sube reusando el
mismo endpoint de thumbnails que ya usa la Library de escenas .blend. Se
cachea localmente por (product_id, view_id, revision) para no regenerar
en cada scan -- solo cuando el contenido de la vista realmente cambia.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from .uv_catalog import resolve_products_root
from .uv_executor import render_uv_preview_png

log = logging.getLogger("kernel-agent.uv-thumbnails")
_THUMB_MAX_DIM = 320


def _cache_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    cache_dir = base / "kernel-agent"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "uv-thumb-cache.json"


def _load_cache() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict[str, dict[str, Any]]) -> None:
    try:
        _cache_path().write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass


def attach_thumbnails(
    products: list[dict[str, Any]],
    products_dir: str,
    api_client: Any,
) -> list[dict[str, Any]]:
    """Agrega `thumbnail_url` a la primera vista de cada producto, in-place.

    Si no hay `generic.png` en la raiz del catalogo, o si un render puntual
    falla, ese producto se deja sin thumbnail sin interrumpir el resto.
    """
    root = resolve_products_root(products_dir)
    generic_path = root / "generic.png"
    if not generic_path.is_file():
        return products

    cache = _load_cache()
    cache_dirty = False

    for product in products:
        views = product.get("views") or []
        if not views:
            continue
        view = views[0]
        key = f"{product['product_id']}:{view['view_id']}"
        entry = cache.get(key)
        if entry and entry.get("revision") == product["revision"] and entry.get("url"):
            view["thumbnail_url"] = entry["url"]
            continue

        try:
            png = render_uv_preview_png(
                products_dir=products_dir,
                product_id=product["product_id"],
                view_id=view["view_id"],
                label_url=str(generic_path),
                state={},
                max_dim=_THUMB_MAX_DIM,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo generar thumbnail de %s: %s", key, exc)
            continue

        hash_hex = hashlib.sha1(png).hexdigest()[:16]
        url = api_client.upload_thumbnail(png, hash_hex)
        if not url:
            log.warning("Thumbnail generado pero upload fallo: %s", key)
            continue

        cache[key] = {"revision": product["revision"], "url": url}
        cache_dirty = True
        view["thumbnail_url"] = url
        log.info("Thumbnail UV generado: %s -> %s", key, url)

    if cache_dirty:
        _save_cache(cache)
    return products
