"""Upload de PNGs a Supabase Storage.

Patrón: el agent NO se conecta directo a Supabase con service_role
(eso sería un secret en cada PC). En su lugar, el server (Vercel) firma
una URL pre-firmada y el agent hace el upload con esa URL temporal.

Para v1 simple: hacemos upload directo al bucket público 'renders'
usando el api_key del agent como auth contra un endpoint proxy
/api/agent/upload (no implementado todavía — fase siguiente).

Por ahora `upload_render` devuelve el path local + un public_url placeholder
que será reemplazado cuando agreguemos el upload real.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UploadResult:
    storage_path: str
    public_url: str | None
    size_bytes: int
    sha256: str
    width: int | None = None
    height: int | None = None


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
    """Lee dimensiones de un PNG sin dependencias externas (parsea header IHDR)."""
    try:
        with path.open("rb") as f:
            header = f.read(24)
        if header[:8] != b"\x89PNG\r\n\x1a\n":
            return (None, None)
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        return (width, height)
    except OSError:
        return (None, None)


def stage_render(
    local_path: Path,
    job_id: str,
    view: str,
) -> UploadResult:
    """Prepara un render local para reportarlo al server.

    Por ahora NO sube físicamente — solo calcula metadata (sha256, tamaño,
    dimensiones). El upload real a Supabase Storage se hace en la fase
    siguiente con bucket policy ajustada para que agents puedan escribir.

    El storage_path es el destino lógico: jobs/<job_id>/<view>.png
    """
    size = local_path.stat().st_size
    sha = _file_sha256(local_path)
    w, h = _image_dimensions(local_path)
    storage_path = f"jobs/{job_id}/{view}.png"
    return UploadResult(
        storage_path=storage_path,
        public_url=None,  # se llenará cuando hagamos upload real
        size_bytes=size,
        sha256=sha,
        width=w,
        height=h,
    )
