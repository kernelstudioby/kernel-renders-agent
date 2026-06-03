"""Daemon principal: loop infinito poll → claim → execute → complete.

Maneja Ctrl+C limpiamente. Reintenta errores de red con backoff.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .api_client import ApiClient, ApiError
from .config import AgentConfig
from .executor import execute_plan
from .library_scan import scan_blend_files
from .storage import upload_render

log = logging.getLogger("kernel-agent.daemon")


class AgentDaemon:
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self.client = ApiClient(cfg.server_url, cfg.api_key)
        self.should_stop = False

    def run(self) -> None:
        log.info("Render Agent online · server=%s", self.cfg.server_url)
        log.info("Polling cada %ss", self.cfg.poll_interval_seconds)

        backoff = self.cfg.poll_interval_seconds
        max_backoff = 60
        while not self.should_stop:
            try:
                self._tick()
                backoff = self.cfg.poll_interval_seconds  # reset
            except ApiError as e:
                log.warning("API error %s: %s — retry in %ss", e.status_code, e.message, backoff)
                if e.status_code == 401:
                    log.error("Token inválido o revocado. Detén el agent y corre `kernel-agent setup`.")
                    return
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
            except KeyboardInterrupt:
                log.info("Interrumpido por usuario. Bye.")
                return
            except Exception as e:  # noqa: BLE001
                log.exception("Error inesperado: %s — retry in %ss", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
            else:
                time.sleep(self.cfg.poll_interval_seconds)

    def _tick(self) -> None:
        """Una iteración: poll + (si hay job) claim + execute + complete."""
        library_scenes = scan_blend_files(self.cfg.library_dir)
        result = self.client.poll(
            gpu_info=self.cfg.gpu_info or None,
            blender_version=self.cfg.blender_version or None,
            library_scenes=library_scenes,
        )
        job = result.get("job")
        if not job:
            return

        job_id = job["id"]
        log.info("Nuevo job %s · %d steps · prompt=%r", job_id, len(job.get("plan", [])), job.get("prompt", "")[:80])

        try:
            claim_resp = self.client.claim(job_id)
        except ApiError as e:
            if e.status_code == 409:
                log.info("Job ya reclamado por otro agent. Sigo polling.")
                return
            raise
        job = claim_resp["job"]

        plan = job.get("plan", [])
        total_steps = len(plan)

        def _on_step(cur: int, total: int, msg: str) -> None:
            try:
                self.client.progress(job_id, cur, total, message=msg)
            except ApiError:
                pass  # no fallar el job por error de telemetría

        try:
            exec_result = execute_plan(
                plan=plan,
                blender_bin=self.cfg.blender_bin,
                on_step_done=_on_step,
                output_dir=self.cfg.output_dir,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Excepción ejecutando plan: %s", e)
            self.client.complete_failure(job_id, f"executor exception: {e}")
            return

        if not exec_result.success:
            failure = exec_result.failed_step or {"error": "render failed without details"}
            err_msg = f"{failure.get('tool', '?')}: {failure.get('error', 'unknown')}"
            log.error("Job %s falló: %s", job_id, err_msg)
            self.client.complete_failure(job_id, err_msg)
            return

        # Éxito: subir outputs (si los hay) a Supabase Storage y reportar
        renders = []
        upload_failures = 0
        for output in exec_result.outputs:
            p = Path(output)
            if not p.exists():
                continue
            view_name = p.stem
            try:
                uploaded = upload_render(p, job_id, view_name, self.client)
                renders.append({
                    "view": view_name,
                    "format": p.suffix.lstrip(".").lower() or "png",
                    "storage_path": uploaded.storage_path,
                    "public_url": uploaded.public_url,
                    "size_bytes": uploaded.size_bytes,
                    "width": uploaded.width,
                    "height": uploaded.height,
                })
                log.info("  ↑ subido %s (%d KB)", view_name, uploaded.size_bytes // 1024)
            except Exception as e:  # noqa: BLE001
                upload_failures += 1
                log.error("Upload de %s falló: %s", view_name, e)

        # Falla solo si: el plan produjo outputs pero TODOS fallaron al subir.
        # Si el plan no produjo outputs (ej. solo inspect_scene), es completed sin renders.
        if exec_result.outputs and not renders:
            log.warning(
                "Job %s produjo %d outputs pero ninguno subió.",
                job_id,
                len(exec_result.outputs),
            )
            self.client.complete_failure(
                job_id, f"all {upload_failures} uploads failed"
            )
            return

        log.info(
            "Job %s OK · %ss · %d renders",
            job_id,
            exec_result.duration_seconds,
            len(renders),
        )
        self.client.complete_success(job_id, renders)
