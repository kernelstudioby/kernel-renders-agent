"""Escaneo de PSDs en `psds_dir` del agent.

Devuelve [{name, path, size_kb, mtime, width, height, preview_url}, ...].
Enriquece con dimensions + preview JPG subido al backend (similar a thumbnails
de .blend).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("kernel-agent.psd_scan")


def scan_psd_files(psds_dir: str | None, api_client: Any | None = None) -> list[dict]:
    """Lista los PSDs del directorio configurado. No recursivo.

    Si `api_client` se pasa, genera+sube un thumbnail JPG por PSD (cache local
    por hash, mismo patrón que blend thumbnails) y reporta `preview_url`.
    """
    if not psds_dir:
        return []
    root = Path(psds_dir)
    if not root.exists() or not root.is_dir():
        return []

    raw: list[dict] = []
    try:
        for entry in sorted(root.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix.lower() != ".psd":
                continue
            try:
                stat = entry.stat()
                size_kb = max(1, stat.st_size // 1024)
                mtime = int(stat.st_mtime)
            except OSError:
                continue
            raw.append({
                "name": entry.stem,
                "path": str(entry.resolve()),
                "size_kb": size_kb,
                "mtime": mtime,
            })
    except OSError:
        return raw

    if not api_client or not raw:
        return raw

    # Enriquecer con dimensions + preview_url (cache disk-backed)
    try:
        from .psd_thumbnail import extract_psd_preview_jpg, get_psd_dimensions
    except ImportError:
        return raw

    cache_path = _thumb_cache_path()
    cache = _load_cache(cache_path)
    cache_dirty = False

    for item in raw:
        path_str = item["path"]
        entry = cache.get(path_str)
        if entry and entry.get("mtime") == item["mtime"]:
            if entry.get("width"):
                item["width"] = entry["width"]
            if entry.get("height"):
                item["height"] = entry["height"]
            if entry.get("preview_url"):
                item["preview_url"] = entry["preview_url"]
            continue

        dims = get_psd_dimensions(Path(path_str))
        width = dims[0] if dims else None
        height = dims[1] if dims else None

        jpg = extract_psd_preview_jpg(Path(path_str), max_side=512)
        preview_url: str | None = None
        hash_hex: str | None = None
        if jpg:
            hash_hex = hashlib.sha1(jpg).hexdigest()[:16]
            # Reuse existing thumb URL if otro PSD generó el mismo bytes
            for v in cache.values():
                if v.get("hash") == hash_hex and v.get("preview_url"):
                    preview_url = v["preview_url"]
                    break
            if preview_url is None:
                preview_url = api_client.upload_thumbnail(jpg, hash_hex)
                if preview_url:
                    log.info("PSD preview subido: %s → %s", Path(path_str).name, preview_url)

        cache[path_str] = {
            "mtime": item["mtime"],
            "width": width,
            "height": height,
            "hash": hash_hex,
            "preview_url": preview_url or "",
        }
        cache_dirty = True
        if width:
            item["width"] = width
        if height:
            item["height"] = height
        if preview_url:
            item["preview_url"] = preview_url

    if cache_dirty:
        _save_cache(cache_path, cache)
    return raw


def _thumb_cache_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    cache_dir = base / "kernel-agent"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "psd-thumb-cache.json"


def _load_cache(p: Path) -> dict[str, dict[str, Any]]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(p: Path, cache: dict[str, dict[str, Any]]) -> None:
    try:
        p.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass
