"""Upload de PNGs a Supabase Storage via signed upload URL.

Flujo:
  1. Agent llama POST /api/agent/upload-url/:job_id { view } → recibe signed_url + storage_path + public_url
  2. Agent hace PUT al signed_url con el binario del PNG
  3. Agent reporta el storage_path + public_url al server via /api/agent/complete

Esto bypass del límite de 4.5 MB de Vercel functions porque el upload va
directo a Supabase Storage, no por el server intermedio.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx


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


def upload_render(
    local_path: Path,
    job_id: str,
    view: str,
    api_client,
    timeout: float = 120.0,
) -> UploadResult:
    """Sube un PNG a Supabase Storage via signed URL.

    Solo PNG. Los .exr quedan en disco local del agent (post-prod) — el daemon
    los filtra antes de llamar esta función.

    Args:
        local_path: path al PNG en disco
        job_id: id del job
        view: nombre lógico del render (ej "render-000deg")
        api_client: instancia de ApiClient para pedir signed URL
        timeout: timeout del PUT (default 120s para PNGs grandes)
    """
    size = local_path.stat().st_size
    sha = _file_sha256(local_path)
    w, h = _image_dimensions(local_path)

    # Determinar content-type según la extensión real del archivo
    ext = local_path.suffix.lower()
    content_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "image/png")
    # Asegurar que el `view` que mandamos al server preserva la extensión
    # correcta (antes hardcodeaba .png aunque el archivo fuera .jpg).
    view_with_ext = view if "." in view else f"{view}{ext}"

    # 1. Pedir signed URL al server
    resp = api_client._request(
        "POST",
        f"/api/agent/upload-url/{job_id}",
        json={"view": view_with_ext, "content_type": content_type},
    )
    signed_url = resp["signed_url"]
    storage_path = resp["storage_path"]
    public_url = resp.get("public_url")

    # 2. PUT al signed URL con el binario
    with local_path.open("rb") as f:
        put_resp = httpx.put(
            signed_url,
            content=f.read(),
            headers={"Content-Type": content_type},
            timeout=timeout,
        )
    if put_resp.status_code >= 400:
        raise RuntimeError(
            f"upload PUT failed [{put_resp.status_code}]: {put_resp.text[:200]}"
        )

    return UploadResult(
        storage_path=storage_path,
        public_url=public_url,
        size_bytes=size,
        sha256=sha,
        width=w,
        height=h,
    )
