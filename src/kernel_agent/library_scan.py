"""Escanea el library_dir local en busca de archivos .blend.

El daemon llama esto cada poll y manda la lista al server, que la expone en
/api/library para que la UI muestre las escenas disponibles sin que el usuario
tenga que pegar rutas absolutas.

Adicionalmente puede enriquecer con view_layers (requiere Blender) para que
la UI muestre un dropdown dinámico por escena en lugar del toggle hardcoded
"dry/sweaty". Los view_layers se cachean en disco vía scene_metadata.

También extrae el thumbnail embebido del .blend (si fue guardado con
"Save Thumbnail" en Save As) y lo sube al backend para que la UI muestre el
preview real del archivo en el ScenePicker, en lugar del último render.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("kernel-agent.library_scan")


def scan_blend_files(library_dir: str) -> list[dict]:
    """Devuelve [{name, path, size_kb}, ...] para cada .blend en library_dir.

    No recursivo: solo el primer nivel. Si library_dir no existe o está vacío
    devuelve lista vacía sin error.
    """
    if not library_dir:
        return []
    root = Path(library_dir)
    if not root.exists() or not root.is_dir():
        return []
    out: list[dict] = []
    try:
        for entry in sorted(root.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix.lower() != ".blend":
                continue
            try:
                size_kb = max(1, entry.stat().st_size // 1024)
            except OSError:
                continue
            out.append({
                "name": entry.stem,
                "path": str(entry.resolve()),
                "size_kb": size_kb,
            })
    except OSError:
        return out
    return out


def scan_blend_files_with_view_layers(
    library_dir: str,
    blender_bin: str,
    output_dir: str | None,
    api_client: Any | None = None,
) -> list[dict]:
    """Como scan_blend_files() pero enriquece cada escena con `view_layers`
    y `thumbnail_url` (PNG embebido en el .blend subido al backend).

    Usa scene_metadata.get_view_layers_for_scenes con cache disk-backed, así
    el costo de abrir Blender se amortiza entre polls. Solo re-escanea si el
    archivo cambió (mtime/size).

    Si `api_client` se pasa, extrae el thumbnail embebido del .blend y lo
    sube al backend. La URL se cachea localmente por hash, así re-subir el
    mismo archivo no genera tráfico.
    """
    scenes = scan_blend_files(library_dir)
    if not scenes or not blender_bin:
        return scenes
    try:
        from .scene_metadata import get_metadata_for_scenes
        meta_map = get_metadata_for_scenes(scenes, blender_bin, output_dir)
    except Exception:  # noqa: BLE001
        meta_map = {}
    thumb_map = _resolve_thumbnails(scenes, api_client) if api_client else {}
    enriched: list[dict] = []
    for s in scenes:
        path_key = str(Path(s["path"]).resolve())
        meta = meta_map.get(path_key, {})
        item = {
            **s,
            "view_layers": meta.get("view_layers", []),
            "cameras": meta.get("cameras", []),
            "active_camera": meta.get("active_camera"),
        }
        thumb_url = thumb_map.get(path_key)
        if thumb_url:
            item["thumbnail_url"] = thumb_url
        enriched.append(item)
    return enriched


def _thumb_cache_path() -> Path:
    """Ruta del caché local de thumbnails (path → mtime + hash + url)."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    cache_dir = base / "kernel-agent"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "thumb-cache.json"


def _load_thumb_cache() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(_thumb_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_thumb_cache(cache: dict[str, dict[str, Any]]) -> None:
    try:
        _thumb_cache_path().write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass


def _resolve_thumbnails(
    scenes: list[dict], api_client: Any
) -> dict[str, str]:
    """Extrae thumbnail de cada .blend y lo sube si no está cacheado.

    Estrategia:
    - Para cada scene path, comprueba mtime. Si no cambió desde la última vez
      y tenemos URL cacheada → reusar.
    - Si cambió o nunca se vio → extraer thumb (pure Python, lee .blend), hash
      el PNG, subir si nuevo. Cachear { mtime, hash, url } por path.
    - Si el .blend no tiene thumbnail embebido (Save Thumbnail desactivado) →
      cachear como "no thumb" para no reintentar cada poll.
    """
    from .blend_thumbnail import extract_blend_thumbnail_png

    cache = _load_thumb_cache()
    result: dict[str, str] = {}
    cache_dirty = False

    for s in scenes:
        path_str = str(Path(s["path"]).resolve())
        try:
            mtime = int(Path(s["path"]).stat().st_mtime)
        except OSError:
            continue

        entry = cache.get(path_str)
        if entry and entry.get("mtime") == mtime:
            url = entry.get("url")
            if url:
                result[path_str] = url
            # entry.url == "" significa "ya intentamos y no hay thumb"; skip.
            continue

        png = extract_blend_thumbnail_png(Path(s["path"]))
        if not png:
            cache[path_str] = {"mtime": mtime, "hash": None, "url": ""}
            cache_dirty = True
            continue

        hash_hex = hashlib.sha1(png).hexdigest()[:16]
        # Si ya subimos este mismo hash antes (otro archivo idéntico), reusar URL.
        prior = next(
            (
                v.get("url")
                for v in cache.values()
                if v.get("hash") == hash_hex and v.get("url")
            ),
            None,
        )
        if prior:
            cache[path_str] = {"mtime": mtime, "hash": hash_hex, "url": prior}
            result[path_str] = prior
            cache_dirty = True
            continue

        url = api_client.upload_thumbnail(png, hash_hex)
        if url:
            cache[path_str] = {"mtime": mtime, "hash": hash_hex, "url": url}
            result[path_str] = url
            cache_dirty = True
            log.info("Thumb subido: %s → %s", Path(s["path"]).name, url)
        else:
            log.warning("Thumb extraído pero upload falló: %s", Path(s["path"]).name)

    if cache_dirty:
        _save_thumb_cache(cache)
    return result
