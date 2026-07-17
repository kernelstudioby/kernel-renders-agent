"""Daemon principal: loop infinito poll → claim → execute → complete.

Maneja Ctrl+C limpiamente. Reintenta errores de red con backoff.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from .api_client import ApiClient, ApiError
from .config import AgentConfig
from .executor import execute_plan
from .library_scan import scan_blend_files_with_view_layers
from .psd_executor import execute_psd_plan, is_psd_plan
from .psd_scan import scan_psd_files
from .storage import upload_blend_for_download, upload_render

log = logging.getLogger("kernel-agent.daemon")


class _Heartbeat:
    """Manda poll() en background mientras un job está corriendo.

    Esto actualiza `agent.last_seen_at` en Supabase para que la UI no
    declare "Sin señal del agent" durante renders largos (EXR multilayer,
    six-packs) que pueden tomar 10-60 min en un solo step.
    """

    def __init__(self, client: ApiClient, cfg: AgentConfig, interval: int = 5):
        self.client = client
        self.cfg = cfg
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="heartbeat")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                # Solo heartbeat — no procesamos el job si llega uno nuevo aquí.
                self.client.poll(
                    gpu_info=self.cfg.gpu_info or None,
                    blender_version=self.cfg.blender_version or None,
                    library_scenes=None,  # no contaminar cada heartbeat con escaneo de disco
                )
            except Exception:  # noqa: BLE001
                # No fallar el render por error de telemetría. Silencioso a propósito.
                pass


class _CancelWatcher:
    """KER-229: mientras un job corre, consulta periódicamente su status en
    el server. Si alguien lo cancela desde la UI, setea `event` — executor.py
    lo revisa en su loop de polling y mata el proceso de Blender.

    Igual que _Heartbeat, corre en su propio thread para no bloquear el
    drenado de stdout de Blender (que es lo que de verdad determina cuándo
    termina el render).
    """

    def __init__(self, client: ApiClient, job_id: str, interval: int = 5):
        self.client = client
        self.job_id = job_id
        self.interval = interval
        self.event = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="cancel-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            status = self.client.get_job_status(self.job_id)
            if status == "cancelled":
                self.event.set()
                return


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
        library_scenes = scan_blend_files_with_view_layers(
            self.cfg.library_dir,
            self.cfg.blender_bin,
            self.cfg.output_dir,
            api_client=self.client,
        )
        library_psds = scan_psd_files(self.cfg.psds_dir, api_client=self.client)
        result = self.client.poll(
            gpu_info=self.cfg.gpu_info or None,
            blender_version=self.cfg.blender_version or None,
            library_scenes=library_scenes,
            library_psds=library_psds,
        )
        blend_download = result.get("blend_download")
        if blend_download:
            self._handle_blend_download(blend_download)

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

        def _on_step(cur: int, total: int, msg: str, extras: dict | None = None) -> None:
            try:
                self.client.progress(job_id, cur, total, message=msg, extras=extras)
            except ApiError:
                pass  # no fallar el job por error de telemetría

        heartbeat = _Heartbeat(self.client, self.cfg, interval=self.cfg.poll_interval_seconds)
        heartbeat.start()
        # KER-229: mientras el render corre, vigilamos si lo cancelaron desde
        # la UI. Solo aplica al path de Blender (execute_plan acepta
        # abort_event) — los jobs de PSD son rápidos y no pasan por aquí.
        cancel_watcher = _CancelWatcher(self.client, job_id, interval=self.cfg.poll_interval_seconds)
        cancel_watcher.start()
        try:
            if is_psd_plan(plan):
                # Job de PSD: no levanta Blender, ejecuta con psd-tools + Pillow
                log.info("Job %s es PSD plan (no Blender)", job_id)
                # Expandir {OUTPUT_DIR} con el output_dir local del agent
                from .executor import _expand_placeholders
                psd_plan = _expand_placeholders(plan, self.cfg.output_dir)
                exec_result = execute_psd_plan(
                    plan=psd_plan, on_step=_on_step, blender_bin=self.cfg.blender_bin
                )
            else:
                exec_result = execute_plan(
                    plan=plan,
                    blender_bin=self.cfg.blender_bin,
                    on_step_done=_on_step,
                    output_dir=self.cfg.output_dir,
                    abort_event=cancel_watcher.event,
                )
        except Exception as e:  # noqa: BLE001
            log.exception("Excepción ejecutando plan: %s", e)
            self.client.complete_failure(job_id, f"executor exception: {e}")
            return
        finally:
            heartbeat.stop()
            cancel_watcher.stop()

        if getattr(exec_result, "cancelled", False):
            # El server ya marcó status='cancelled' cuando el usuario le dio
            # click a Cancelar — no llamamos complete_success/complete_failure
            # para no pisarlo de vuelta a 'failed'.
            log.info("Job %s cancelado por el usuario — proceso de Blender terminado", job_id)
            return

        # Mostrar líneas de diagnóstico de Blender en la consola del daemon
        # (especialmente útiles para Moy: qué cámara se usó, qué view_layer,
        # qué workaround de render). Solo emitimos las marcadas con prefijos
        # para no spammear con todas las líneas de Cycles (samples, BVH, etc.)
        _DIAG_PREFIXES = (
            "[render setup]",
            "[render EXR]",
            "[render fresh]",
            "[setup]",
            "[step ",
        )
        for line in exec_result.stdout.splitlines():
            stripped = line.strip()
            if any(stripped.startswith(p) for p in _DIAG_PREFIXES):
                log.info("  %s", stripped)

        if not exec_result.success:
            failure = exec_result.failed_step or {"error": "render failed without details"}
            err_msg = f"{failure.get('tool', '?')}: {failure.get('error', 'unknown')}"
            log.error("Job %s falló: %s", job_id, err_msg)
            self.client.complete_failure(job_id, err_msg)
            return

        # Éxito: separar outputs en uploadables (PNG/JPG/WEBP) y locales (EXR).
        # Los EXR se quedan en disco del agent — workflow de post-prod local.
        UPLOADABLE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
        renders = []
        upload_failures = 0
        upload_attempted = 0
        local_artifacts: list[str] = []

        for output in exec_result.outputs:
            p = Path(output)
            if not p.exists():
                continue
            ext = p.suffix.lower()
            view_name = p.stem

            if ext not in UPLOADABLE_EXTS:
                # EXR u otro formato local. No subir; reportar como artefacto local.
                local_artifacts.append(str(p))
                log.info(
                    "  📁 local %s (%s, %d KB) — no se sube",
                    view_name,
                    ext,
                    p.stat().st_size // 1024,
                )
                continue

            upload_attempted += 1
            try:
                uploaded = upload_render(p, job_id, view_name, self.client)
                renders.append({
                    "view": view_name,
                    "format": ext.lstrip("."),
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

        if local_artifacts:
            log.info(
                "Job %s · %d artefacto(s) local(es) (EXR/etc.) en %s",
                job_id,
                len(local_artifacts),
                self.cfg.output_dir or "(?)",
            )

        # Falla solo si: el plan INTENTÓ subir outputs y todos fallaron.
        # Si el plan no produjo nada uploadable (solo EXR, solo inspect_scene),
        # es completed sin renders.
        if upload_attempted > 0 and not renders:
            log.warning(
                "Job %s intentó subir %d outputs pero todos fallaron.",
                job_id,
                upload_attempted,
            )
            self.client.complete_failure(
                job_id, f"all {upload_failures} uploads failed"
            )
            return

        log.info(
            "Job %s OK · %ss · %d renders subidos · %d locales",
            job_id,
            exec_result.duration_seconds,
            len(renders),
            len(local_artifacts),
        )
        self.client.complete_success(job_id, renders)

    def _handle_blend_download(self, blend_download: dict) -> None:
        """Sube un .blend en bruto para que el usuario lo baje desde Library
        (KER-190). Se resuelve antes que el job de render de este mismo tick
        para no competir con el heartbeat de un render largo en el próximo
        poll, pero después de haber hecho ya el poll con el heartbeat fresco.

        No usa claim/heartbeat como los jobs de render: el server solo
        entrega esta solicitud al agent dueño del scene_path, así que no
        hay riesgo de que dos agents la tomen a la vez.
        """
        download_id = blend_download["id"]
        scene_path = blend_download.get("scene_path", "")
        log.info("Descarga de .blend pendiente %s · %s", download_id, scene_path)

        local_path = Path(scene_path)
        if not local_path.is_file():
            msg = f"archivo no encontrado en disco: {scene_path}"
            log.error("Descarga %s falló: %s", download_id, msg)
            try:
                self.client.fail_blend_download(download_id, msg)
            except ApiError:
                pass
            return

        try:
            uploaded = upload_blend_for_download(local_path, download_id, self.client)
            self.client.complete_blend_download(download_id, uploaded.size_bytes)
            log.info(
                "Descarga %s lista · %d MB subidos",
                download_id,
                uploaded.size_bytes // (1024 * 1024),
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Descarga %s falló: %s", download_id, e)
            try:
                self.client.fail_blend_download(download_id, str(e))
            except ApiError:
                pass
