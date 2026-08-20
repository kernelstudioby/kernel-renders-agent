"""API loopback de preview UV para interacción web en tiempo real."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .config import AgentConfig
from .uv_executor import render_uv_preview_png

log = logging.getLogger("kernel-agent.uv-preview")
_MAX_BODY_BYTES = 128 * 1024


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""


def allowed_origins(server_url: str) -> set[str]:
    origins = {
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    }
    configured = _origin(server_url)
    if configured:
        origins.add(configured)
    return origins


class UvPreviewServer:
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._origins = allowed_origins(cfg.server_url)

    @property
    def port(self) -> int:
        if self._httpd is not None:
            return int(self._httpd.server_port)
        return self.cfg.uv_preview_port

    def start(self) -> bool:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "KernelUvPreview/1.0"

            def log_message(self, fmt: str, *args: Any) -> None:
                log.debug(fmt, *args)

            def _cors(self, origin: str) -> None:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Vary", "Origin")

            def _origin_allowed(self) -> tuple[bool, str]:
                origin = self.headers.get("Origin", "")
                return origin in owner._origins, origin

            def _json_error(self, status: int, message: str, origin: str = "") -> None:
                payload = json.dumps({"error": message}).encode("utf-8")
                self.send_response(status)
                if origin in owner._origins:
                    self._cors(origin)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def do_OPTIONS(self) -> None:  # noqa: N802
                allowed, origin = self._origin_allowed()
                if not allowed:
                    self._json_error(403, "origin no autorizado")
                    return
                self.send_response(204)
                self._cors(origin)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                if self.path != "/health":
                    self._json_error(404, "ruta no encontrada")
                    return
                payload = json.dumps({"ok": True, "service": "uv-preview"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self) -> None:  # noqa: N802
                allowed, origin = self._origin_allowed()
                if not allowed:
                    self._json_error(403, "origin no autorizado")
                    return
                if self.path != "/v1/preview":
                    self._json_error(404, "ruta no encontrada", origin)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size <= 0 or size > _MAX_BODY_BYTES:
                        raise ValueError("payload inválido")
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise ValueError("payload inválido")
                    state = body.get("state", {})
                    if not isinstance(state, dict):
                        raise ValueError("state inválido")
                    png = render_uv_preview_png(
                        products_dir=owner.cfg.uv_products_dir,
                        product_id=str(body["product_id"]),
                        view_id=str(body["view_id"]),
                        label_url=str(body["label_url"]),
                        texture_checksum=str(body.get("texture_checksum") or "") or None,
                        state=state,
                        max_dim=int(body.get("max_dim", 640)),
                        cache_max_mb=owner.cfg.uv_cache_max_mb,
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self._json_error(400, str(exc), origin)
                    return
                except Exception as exc:  # noqa: BLE001
                    log.exception("No se pudo generar preview UV")
                    self._json_error(500, f"{type(exc).__name__}: {exc}", origin)
                    return

                try:
                    self.send_response(200)
                    self._cors(origin)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(png)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(png)
                except (BrokenPipeError, ConnectionResetError):
                    # El browser aborta previews anteriores cuando el slider
                    # vuelve a moverse; el resultado obsoleto se descarta.
                    return

        try:
            self._httpd = ThreadingHTTPServer(("127.0.0.1", self.cfg.uv_preview_port), Handler)
        except OSError as exc:
            log.warning(
                "Preview UV local no disponible en puerto %s: %s",
                self.cfg.uv_preview_port,
                exc,
            )
            return False
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            daemon=True,
            name="uv-preview-http",
        )
        self._thread.start()
        log.info("Preview UV local online · http://127.0.0.1:%s", self.port)
        return True

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
