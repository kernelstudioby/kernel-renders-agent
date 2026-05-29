"""Escanea el library_dir local en busca de archivos .blend.

El daemon llama esto cada poll y manda la lista al server, que la expone en
/api/library para que la UI muestre las escenas disponibles sin que el usuario
tenga que pegar rutas absolutas.
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
