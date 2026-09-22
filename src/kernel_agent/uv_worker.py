"""Carril UV independiente dentro del mismo proceso/identidad del agent."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import suppress
from pathlib import Path

from .api_client import ApiClient, ApiError
from .config import AgentConfig
from .storage import upload_render
from .uv_catalog import scan_uv_products
from .uv_executor import execute_uv_plan, is_uv_plan, render_uv_preview_png
from .uv_thumbnails import attach_thumbnails

log = logging.getLogger("kernel-agent.uv-worker")


class UvWorker:
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._catalog_signature = ""
        self._catalog: list[dict] = []
        self._next_catalog_scan = 0.0
        self._last_catalog_sync = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="uv-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.cfg.poll_interval_seconds + 10)

    def _run(self) -> None:
        with ApiClient(self.cfg.server_url, self.cfg.api_key) as client:
            backoff = self.cfg.poll_interval_seconds
            while not self._stop.is_set():
                try:
                    now = time.monotonic()
                    if now >= self._next_catalog_scan:
                        catalog = scan_uv_products(self.cfg.uv_products_dir)
                        with suppress(Exception):
                            catalog = attach_thumbnails(catalog, self.cfg.uv_products_dir, client)
                        self._catalog = catalog
                        self._next_catalog_scan = now + 60
                    products = self._catalog
                    signature = "|".join(
                        f"{product['product_id']}:{product['revision']}" for product in products
                    )
                    # Además de cambios, refrescar presencia cada 30 s para que
                    # uv_agent_products.last_seen_at no expire en la UI.
                    if signature != self._catalog_signature or now - self._last_catalog_sync >= 30:
                        client.sync_uv_catalog(products)
                        self._catalog_signature = signature
                        self._last_catalog_sync = now
                        log.info("Catálogo UV sincronizado · %d productos", len(products))
                    result = client.poll(
                        gpu_info=self.cfg.gpu_info or None,
                        capability="uv",
                        uv_product_ids=[product["product_id"] for product in products],
                    )
                    job = result.get("job")
                    if job:
                        self._handle_job(client, job)
                    self._handle_pending_preview(client)
                    backoff = self.cfg.poll_interval_seconds
                except ApiError as exc:
                    log.warning("UV API error %s: %s", exc.status_code, exc.message)
                    if exc.status_code == 401:
                        return
                    backoff = min(backoff * 2, 60)
                except Exception:  # noqa: BLE001
                    log.exception("Error inesperado en UV lane")
                    backoff = min(backoff * 2, 60)
                self._stop.wait(backoff)

    def _handle_job(self, client: ApiClient, job: dict) -> None:
        job_id = job["id"]
        try:
            claimed = client.claim(job_id)["job"]
        except ApiError as exc:
            if exc.status_code == 409:
                return
            raise
        plan = claimed.get("plan", [])
        if not is_uv_plan(plan):
            client.complete_failure(job_id, "job no pertenece al UV lane")
            return

        def progress(current: int, total: int, message: str) -> None:
            with suppress(ApiError):
                client.progress(job_id, current, total, message=message)

        result = execute_uv_plan(
            plan,
            self.cfg.uv_products_dir,
            self.cfg.output_dir,
            self.cfg.uv_cache_max_mb,
            on_step=progress,
        )
        if not result.success:
            failure = result.failed_step or {"error": "UV compose failed"}
            client.complete_failure(job_id, str(failure.get("error")))
            return

        renders: list[dict] = []
        for output in result.outputs:
            path = Path(output)
            uploaded = upload_render(path, job_id, path.stem, client)
            renders.append(
                {
                    "view": path.stem,
                    "format": path.suffix.lstrip("."),
                    "storage_path": uploaded.storage_path,
                    "public_url": uploaded.public_url,
                    "size_bytes": uploaded.size_bytes,
                    "width": uploaded.width,
                    "height": uploaded.height,
                }
            )
        client.complete_success(job_id, renders)
        log.info("UV job %s OK · %.2fs", job_id, result.duration_seconds)

    def _handle_pending_preview(self, client: ApiClient) -> None:
        """KER3-43: atiende un pedido de preview UV remoto, si hay uno.

        Reusa exactamente `render_uv_preview_png` — la misma función que ya
        usa el servidor loopback (127.0.0.1:8765) — así que el resultado es
        idéntico sin importar si el browser está en esta PC o no.
        """
        try:
            request = client.fetch_pending_uv_preview()
        except ApiError:
            return
        if not request:
            return
        request_id = request["id"]
        try:
            png = render_uv_preview_png(
                products_dir=self.cfg.uv_products_dir,
                product_id=str(request["product_id"]),
                view_id=str(request["view_id"]),
                label_url=str(request["label_url"]),
                texture_checksum=str(request.get("texture_checksum") or "") or None,
                state=request.get("state") or {},
                max_dim=int(request.get("max_dim", 640)),
                cache_max_mb=self.cfg.uv_cache_max_mb,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Preview UV remoto %s falló: %s", request_id, exc)
            with suppress(ApiError):
                client.fail_uv_preview(request_id, f"{type(exc).__name__}: {exc}")
            return
        try:
            client.complete_uv_preview(request_id, png)
        except ApiError as exc:
            log.warning("No se pudo subir preview UV remoto %s: %s", request_id, exc)
