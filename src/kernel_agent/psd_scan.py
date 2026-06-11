"""Escaneo de PSDs en `psds_dir` del agent.

Devuelve [{name, path, size_kb, mtime}, ...] similar a library_scan para .blend.
"""

from __future__ import annotations

from pathlib import Path


def scan_psd_files(psds_dir: str | None) -> list[dict]:
    """Lista los PSDs del directorio configurado. No recursivo.

    Si psds_dir es None o no existe, devuelve []. No es un error.
    """
    if not psds_dir:
        return []
    root = Path(psds_dir)
    if not root.exists() or not root.is_dir():
        return []
    out: list[dict] = []
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
            out.append({
                "name": entry.stem,
                "path": str(entry.resolve()),
                "size_kb": size_kb,
                "mtime": mtime,
            })
    except OSError:
        return out
    return out
