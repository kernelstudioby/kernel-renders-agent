"""Escanea el library_dir local en busca de archivos .blend.

El daemon llama esto cada poll y manda la lista al server, que la expone en
/api/library para que la UI muestre las escenas disponibles sin que el usuario
tenga que pegar rutas absolutas.

Adicionalmente puede enriquecer con view_layers (requiere Blender) para que
la UI muestre un dropdown dinámico por escena en lugar del toggle hardcoded
"dry/sweaty". Los view_layers se cachean en disco vía scene_metadata.
"""

from __future__ import annotations

from pathlib import Path


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
) -> list[dict]:
    """Como scan_blend_files() pero enriquece cada escena con `view_layers`.

    Usa scene_metadata.get_view_layers_for_scenes con cache disk-backed, así
    el costo de abrir Blender se amortiza entre polls. Solo re-escanea si el
    archivo cambió (mtime/size).
    """
    scenes = scan_blend_files(library_dir)
    if not scenes or not blender_bin:
        return scenes
    try:
        from .scene_metadata import get_view_layers_for_scenes
        vl_map = get_view_layers_for_scenes(scenes, blender_bin, output_dir)
    except Exception:  # noqa: BLE001
        return scenes
    enriched: list[dict] = []
    for s in scenes:
        path_key = str(Path(s["path"]).resolve())
        vls = vl_map.get(path_key, [])
        enriched.append({**s, "view_layers": vls})
    return enriched
