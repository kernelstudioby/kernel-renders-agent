"""Cliente HTTP del agent contra la plataforma web (Next.js en Vercel).

Endpoints que consume:
  GET    /api/agent/poll              — busca job + heartbeat
  POST   /api/agent/claim/:id         — toma job atómicamente
  POST   /api/agent/progress/:id      — reporta avance
  POST   /api/agent/complete/:id      — sube outputs + marca completed

Toda request lleva header `x-api-key: <token>`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class ApiError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(f"[{status_code}] {message}")
        self.status_code = status_code
        self.message = message


class ApiClient:
    def __init__(self, server_url: str, api_key: str, timeout: float = 30.0):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(
            timeout=timeout,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
        )

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        url = f"{self.server_url}{path}"
        try:
            resp = self._client.request(method, url, **kwargs)
        except httpx.RequestError as e:
            raise ApiError(0, f"network error: {e}") from e
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", resp.text)
            except json.JSONDecodeError:
                detail = resp.text
            raise ApiError(resp.status_code, str(detail))
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {}

    # --- Endpoints ---

    def poll(
        self,
        gpu_info: dict | None = None,
        blender_version: str | None = None,
        library_scenes: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Hace heartbeat al server y pide el siguiente job. Devuelve {agent, job?}.

        library_scenes: lista de .blend en library_dir. Cada item:
            { "name": str, "path": str, "size_kb": int }
        El server los expone en /api/library para que la UI los muestre.
        """
        params = {}
        if gpu_info:
            params["gpu"] = json.dumps(gpu_info)
        if blender_version:
            params["blender"] = blender_version
        if library_scenes is not None:
            params["library"] = json.dumps(library_scenes)
        return self._request("GET", "/api/agent/poll", params=params)

    def claim(self, job_id: str) -> dict[str, Any]:
        """Reclama un job atómicamente. Devuelve {job} o 409 si otro agent ya lo tomó."""
        return self._request("POST", f"/api/agent/claim/{job_id}")

    def progress(
        self,
        job_id: str,
        current_step: int,
        total: int,
        message: str = "",
        status: str = "running",
    ) -> dict[str, Any]:
        """Reporta progreso al server. Status: 'claimed' o 'running'."""
        return self._request(
            "POST",
            f"/api/agent/progress/{job_id}",
            json={
                "current_step": current_step,
                "total": total,
                "message": message,
                "status": status,
            },
        )

    def complete_success(self, job_id: str, renders: list[dict[str, Any]]) -> dict[str, Any]:
        """Marca job como completed con sus outputs."""
        return self._request(
            "POST",
            f"/api/agent/complete/{job_id}",
            json={"renders": renders},
        )

    def complete_failure(self, job_id: str, error: str) -> dict[str, Any]:
        """Marca job como failed con un mensaje de error."""
        return self._request(
            "POST",
            f"/api/agent/complete/{job_id}",
            json={"error": error[:2000]},  # cap length
        )

    def upload_thumbnail(self, png_bytes: bytes, hash_hex: str) -> str | None:
        """Sube el thumbnail extraído de un .blend; devuelve URL pública o None.

        El backend usa el hash como nombre de archivo en Storage, así que
        thumbnails idénticos (mismo PNG) se deduplican naturalmente entre
        agents y escenas.
        """
        import base64
        try:
            resp = self._request(
                "POST",
                "/api/agent/thumbnail",
                json={
                    "hash": hash_hex,
                    "png_base64": base64.b64encode(png_bytes).decode("ascii"),
                },
            )
        except ApiError:
            return None
        url = resp.get("url")
        return url if isinstance(url, str) else None

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
