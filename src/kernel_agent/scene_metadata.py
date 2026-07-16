"""Lee view_layers + cámaras + fotogramas animados de cada .blend para que la
UI muestre selectores dinámicos en lugar de toggles hardcoded.

Abrir Blender headless por cada .blend tarda 3-10s. Cacheamos en disco por
(path, mtime, size_bytes) para que solo se re-escanee cuando el archivo cambie.

Cache: <output_dir o tempdir>/.scene_meta_cache.json
Forma: {
  "<abs path>": {
    "mtime": N,
    "size": N,
    "view_layers": [...],
    "cameras": [{"name": "Camera_Front", "is_active": True, "lens_mm": 50.0}],
    "rotation_frames": [1, 2, 3, 4],
    "scanned_at": N
  },
  ...
}

KER-273: `rotation_frames` son los keyframes del turntable del producto (ej.
"vamos a rotar el producto usando los fotogramas 1/2/3/4 en vez de armar
cámaras extra"). Verificado contra 3 escenas reales de Beyond (2.75L, 250ml
6-pack, lata 354ml): el objeto que rota el producto siempre es un EMPTY con
al menos un hijo (la geometría del envase — bottle/cap/label/can). Cámaras y
luces también suelen tener 2-3 keyframes sueltos (ajustes de encuadre), pero
nunca tienen hijos — por eso se descartan como ruido. Si hay varios EMPTY
animados con hijos, se toma el que más hijos tiene (el turntable principal).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


_BLENDER_PROBE_SCRIPT = r"""
import bpy, json
s = bpy.context.scene
vls = [vl.name for vl in s.view_layers]
active_cam_name = s.camera.name if s.camera else None
cams = []
for o in bpy.data.objects:
    if o.type != 'CAMERA':
        continue
    lens = None
    try:
        lens = float(o.data.lens)
    except Exception:
        pass
    cams.append({
        'name': o.name,
        'is_active': (o is s.camera),
        'lens_mm': lens,
    })

def keyframes_for_action(action, slot):
    frames = set()
    if getattr(action, 'is_action_legacy', False):
        for fc in action.fcurves:
            for kp in fc.keyframe_points:
                frames.add(int(round(kp.co[0])))
        return frames
    for layer in action.layers:
        for strip in layer.strips:
            if strip.type != 'KEYFRAME':
                continue
            try:
                cb = strip.channelbag(slot, ensure=False)
            except Exception:
                cb = None
            if not cb:
                continue
            for fc in cb.fcurves:
                for kp in fc.keyframe_points:
                    frames.add(int(round(kp.co[0])))
    return frames

children_count = {}
for o in bpy.data.objects:
    if o.parent:
        children_count[o.parent.name] = children_count.get(o.parent.name, 0) + 1

best_empty = None
best_children = 0
for o in bpy.data.objects:
    if o.type != 'EMPTY':
        continue
    ad = o.animation_data
    if not ad or not ad.action:
        continue
    n_children = children_count.get(o.name, 0)
    if n_children == 0:
        continue
    frames = keyframes_for_action(ad.action, getattr(ad, 'action_slot', None))
    if not frames:
        continue
    if n_children > best_children:
        best_children = n_children
        best_empty = sorted(frames)

out = {
    'view_layers': vls,
    'cameras': cams,
    'active_camera': active_cam_name,
    'rotation_frames': best_empty or [],
}
print('KERNEL_META_JSON:' + json.dumps(out))
"""


def _cache_path(output_dir: str | None) -> Path:
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


_EMPTY_METADATA: dict[str, Any] = {
    "view_layers": [],
    "cameras": [],
    "active_camera": None,
    "rotation_frames": [],
}


def _probe_metadata(blender_bin: str, blend_path: str, timeout: int = 60) -> dict[str, Any]:
    """Lanza Blender headless contra el .blend y extrae view_layers + cameras + rotation_frames."""
    try:
        result = subprocess.run(
            [blender_bin, "--background", blend_path, "--python-expr", _BLENDER_PROBE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return dict(_EMPTY_METADATA)
    for line in result.stdout.splitlines():
        if line.startswith("KERNEL_META_JSON:"):
            try:
                payload = json.loads(line[len("KERNEL_META_JSON:"):])
                if isinstance(payload, dict):
                    return {
                        "view_layers": [str(v) for v in payload.get("view_layers", [])],
                        "cameras": [
                            {
                                "name": str(c.get("name", "")),
                                "is_active": bool(c.get("is_active", False)),
                                "lens_mm": (
                                    float(c["lens_mm"])
                                    if c.get("lens_mm") is not None
                                    else None
                                ),
                            }
                            for c in (payload.get("cameras") or [])
                            if isinstance(c, dict) and c.get("name")
                        ],
                        "active_camera": payload.get("active_camera"),
                        "rotation_frames": sorted(
                            {int(f) for f in (payload.get("rotation_frames") or [])}
                        ),
                    }
            except ValueError:
                pass
    return dict(_EMPTY_METADATA)


def get_view_layers_for_scenes(
    scenes: list[dict],
    blender_bin: str,
    output_dir: str | None,
) -> dict[str, list[str]]:
    """Compat: devuelve solo view_layers por path. Mantenido para compatibilidad.

    Internamente delega en get_metadata_for_scenes.
    """
    meta = get_metadata_for_scenes(scenes, blender_bin, output_dir)
    return {k: v.get("view_layers", []) for k, v in meta.items()}


def get_metadata_for_scenes(
    scenes: list[dict],
    blender_bin: str,
    output_dir: str | None,
) -> dict[str, dict[str, Any]]:
    """Para cada scene devuelve { view_layers, cameras, active_camera, rotation_frames }.

    Usa cache disk-backed; solo abre Blender si el archivo cambió desde el
    último escaneo. Cache anterior que no tenía alguno de los campos nuevos
    (`cameras`, `rotation_frames`) se re-escanea automáticamente.
    """
    cache_path = _cache_path(output_dir)
    cache = _load_cache(cache_path)
    result: dict[str, dict[str, Any]] = {}
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

        # Cache hit: mismo mtime + size Y tiene los campos nuevos
        if (
            entry
            and entry.get("mtime") == int(stat.st_mtime)
            and entry.get("size") == stat.st_size
            and isinstance(entry.get("view_layers"), list)
            and isinstance(entry.get("cameras"), list)
            and isinstance(entry.get("rotation_frames"), list)
        ):
            result[cache_key] = {
                "view_layers": entry["view_layers"],
                "cameras": entry["cameras"],
                "active_camera": entry.get("active_camera"),
                "rotation_frames": entry["rotation_frames"],
            }
            continue

        # Cache miss: probe con Blender
        meta = _probe_metadata(blender_bin, path_str)
        cache[cache_key] = {
            "mtime": int(stat.st_mtime),
            "size": stat.st_size,
            "view_layers": meta["view_layers"],
            "cameras": meta["cameras"],
            "active_camera": meta["active_camera"],
            "rotation_frames": meta["rotation_frames"],
            "scanned_at": int(time.time()),
        }
        result[cache_key] = {
            "view_layers": meta["view_layers"],
            "cameras": meta["cameras"],
            "active_camera": meta["active_camera"],
            "rotation_frames": meta["rotation_frames"],
        }
        cache_dirty = True

    if cache_dirty:
        _save_cache(cache_path, cache)

    return result
