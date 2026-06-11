"""Lee view_layers de cada .blend para que la UI muestre un dropdown dinámico
en lugar del toggle hardcoded "dry/sweaty".

Abrir Blender headless por cada .blend tarda 3-10s. Cacheamos en disco por
(path, mtime, size_bytes) para que solo se re-escanee cuando el archivo cambie.

Cache: <output_dir o tempdir>/.scene_meta_cache.json
Forma: { "<abs path>": {"mtime": N, "size": N, "view_layers": [...]}, ... }
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path


_BLENDER_PROBE_SCRIPT = r"""
import bpy, json
s = bpy.context.scene
vls = []
for vl in s.view_layers:
    vls.append(vl.name)
print('KERNEL_VL_JSON:' + json.dumps(vls))
"""


def _cache_path(output_dir: str | None) -> Path:
    """Devuelve la ruta del cache. Prefer output_dir; fallback tempdir."""
    if output_dir:
        d = Path(output_dir)
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d / ".scene_meta_cache.json"
        except OSError:
            pass
    return Path(tempfile.gettempdir()) / "kernel_agent_scene_meta_cache.json"


def _load_cache(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(p: Path, cache: dict) -> None:
    try:
        p.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except OSError:
        pass


def _probe_view_layers(blender_bin: str, blend_path: str, timeout: int = 60) -> list[str]:
    """Lanza Blender headless contra el .blend y extrae view_layers."""
    try:
        result = subprocess.run(
            [blender_bin, "--background", blend_path, "--python-expr", _BLENDER_PROBE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    for line in result.stdout.splitlines():
        if line.startswith("KERNEL_VL_JSON:"):
            try:
                payload = json.loads(line[len("KERNEL_VL_JSON:"):])
                if isinstance(payload, list):
                    return [str(v) for v in payload]
            except ValueError:
                pass
    return []


def get_view_layers_for_scenes(
    scenes: list[dict],
    blender_bin: str,
    output_dir: str | None,
) -> dict[str, list[str]]:
    """Para cada scene en `scenes` devuelve sus view_layers.

    scenes: lista [{name, path, size_kb}, ...] del library_scan.
    Returns: {abs_path: [view_layer_names]}.

    Usa cache disk-backed; solo abre Blender si el archivo cambió desde
    el último escaneo. Si Blender no responde, devuelve [] para esa escena.
    """
    cache_path = _cache_path(output_dir)
    cache = _load_cache(cache_path)
    result: dict[str, list[str]] = {}
    cache_dirty = False

    for scene in scenes:
        path_str = scene.get("path") or ""
        if not path_str:
            continue
        p = Path(path_str)
        if not p.exists():
            continue
        stat = p.stat()
        cache_key = str(p.resolve())
        entry = cache.get(cache_key)

        # Cache hit si mismo mtime + size
        if (
            entry
            and entry.get("mtime") == int(stat.st_mtime)
            and entry.get("size") == stat.st_size
            and isinstance(entry.get("view_layers"), list)
        ):
            result[cache_key] = entry["view_layers"]
            continue

        # Cache miss: probe con Blender
        view_layers = _probe_view_layers(blender_bin, path_str)
        cache[cache_key] = {
            "mtime": int(stat.st_mtime),
            "size": stat.st_size,
            "view_layers": view_layers,
            "scanned_at": int(time.time()),
        }
        result[cache_key] = view_layers
        cache_dirty = True

    if cache_dirty:
        _save_cache(cache_path, cache)

    return result
