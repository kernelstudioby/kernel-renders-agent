"""Smoke test del Render Agent.

Para correr en cualquier máquina con el agent instalado:

    cd kernel-renders-agent
    git pull
    python -m pip install -e .
    python smoke_test.py

Verifica que los cambios recientes están en el código, que los módulos importan,
que la config está completa, y que el server responde. NO lanza Blender ni
ejecuta jobs — solo valida que el daemon ARRANCARÍA bien.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

# Forzar UTF-8 en Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


PASS = "[OK]"
FAIL = "[X]"
WARN = "[!]"


class SmokeResult:
    def __init__(self) -> None:
        self.checks: list[tuple[str, str, str]] = []  # (status, name, detail)

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.checks.append((status, name, detail))
        print(f"  {status} {name}{(' — ' + detail) if detail else ''}", flush=True)

    def summary(self) -> int:
        total = len(self.checks)
        ok = sum(1 for s, _, _ in self.checks if s == PASS)
        fail = sum(1 for s, _, _ in self.checks if s == FAIL)
        warn = sum(1 for s, _, _ in self.checks if s == WARN)
        print()
        print(f"  Total: {total} · OK: {ok} · WARN: {warn} · FAIL: {fail}")
        return 0 if fail == 0 else 1


def section(title: str) -> None:
    print()
    print(f"=== {title} ===")


def main() -> int:
    print("=" * 60)
    print("  Kernel Renders Agent — Smoke Test")
    print("=" * 60)
    r = SmokeResult()

    # ── 1. Imports básicos ─────────────────────────────────────────────
    section("1. Imports del paquete")
    try:
        from kernel_agent import __version__ as agent_version
        r.add(PASS, "kernel_agent importable", f"v{agent_version}")
    except Exception as e:  # noqa: BLE001
        r.add(FAIL, "kernel_agent importable", str(e))
        return r.summary()

    for mod_name in (
        "kernel_agent.config",
        "kernel_agent.api_client",
        "kernel_agent.daemon",
        "kernel_agent.storage",
        "kernel_agent.library_scan",
        "kernel_agent.asset_materializer",
        "kernel_agent.executor",
        "kernel_agent.cli",
    ):
        try:
            __import__(mod_name)
            r.add(PASS, mod_name)
        except Exception as e:  # noqa: BLE001
            r.add(FAIL, mod_name, f"{type(e).__name__}: {e}")

    # ── 2. Cambios recientes presentes ─────────────────────────────────
    section("2. Cambios recientes presentes")

    # 2a. EXR support en render_views
    try:
        from kernel_scripts import render_views
        if hasattr(render_views, "_FORMAT_ALIASES"):
            r.add(PASS, "render_views._FORMAT_ALIASES", f"{list(render_views._FORMAT_ALIASES.keys())}")
        else:
            r.add(FAIL, "render_views._FORMAT_ALIASES", "no presente — falta git pull")
        # Firma de run_render_one_view debe incluir output_format
        import inspect
        sig = inspect.signature(render_views.run_render_one_view)
        if "output_format" in sig.parameters:
            r.add(PASS, "render_one_view tiene parámetro output_format")
        else:
            r.add(FAIL, "render_one_view sin output_format", "git pull pendiente")
    except Exception as e:  # noqa: BLE001
        r.add(FAIL, "render_views inspección", str(e))

    # 2b. Daemon: UPLOADABLE_EXTS split
    try:
        daemon_src = Path(__import__("kernel_agent.daemon", fromlist=["__file__"]).__file__).read_text(
            encoding="utf-8"
        )
        if "UPLOADABLE_EXTS" in daemon_src and "local_artifacts" in daemon_src:
            r.add(PASS, "daemon separa PNG (upload) vs EXR (local)")
        else:
            r.add(FAIL, "daemon EXR split", "git pull pendiente")
    except Exception as e:  # noqa: BLE001
        r.add(FAIL, "daemon source check", str(e))

    # 2c. set_view_layer aplica hide_render a collections
    try:
        from kernel_scripts import set_view_layer
        src = Path(set_view_layer.__file__).read_text(encoding="utf-8")
        if "collection.hide_render" in src or "coll.hide_render" in src:
            r.add(PASS, "set_view_layer propaga visibilidad a collections")
        else:
            r.add(WARN, "set_view_layer sin fix de hide_render", "git pull pendiente?")
    except Exception as e:  # noqa: BLE001
        r.add(FAIL, "set_view_layer source check", str(e))

    # 2d. {OUTPUT_DIR} placeholder expansion
    try:
        from kernel_agent import executor
        src = Path(executor.__file__).read_text(encoding="utf-8")
        if "{OUTPUT_DIR}" in src and "_expand_placeholders" in src:
            r.add(PASS, "executor expande {OUTPUT_DIR}")
        else:
            r.add(FAIL, "{OUTPUT_DIR} no soportado", "git pull pendiente")
    except Exception as e:  # noqa: BLE001
        r.add(FAIL, "executor source check", str(e))

    # ── 3. Config local ────────────────────────────────────────────────
    section("3. Configuración local del agent")
    try:
        from kernel_agent.config import load_config, is_configured, config_path
        cfg = load_config()
        r.add(PASS, f"config file", str(config_path()))

        if cfg.server_url:
            r.add(PASS, f"server_url", cfg.server_url)
        else:
            r.add(FAIL, "server_url", "vacío — corre kernel-agent setup")

        if cfg.api_key and cfg.api_key.startswith("kr_agent_"):
            r.add(PASS, f"api_key", cfg.api_key[:16] + "...")
        else:
            r.add(FAIL, "api_key", "vacío o inválido")

        if cfg.blender_bin and Path(cfg.blender_bin).exists():
            r.add(PASS, "blender_bin existe", cfg.blender_bin)
        else:
            r.add(FAIL, "blender_bin no existe", cfg.blender_bin or "(vacío)")

        if cfg.library_dir and Path(cfg.library_dir).exists():
            n_blends = len(list(Path(cfg.library_dir).glob("*.blend")))
            r.add(PASS, "library_dir existe", f"{cfg.library_dir} ({n_blends} .blend)")
        else:
            r.add(WARN, "library_dir vacío o no existe", cfg.library_dir or "(vacío)")

        if cfg.output_dir:
            Path(cfg.output_dir).mkdir(parents=True, exist_ok=True)
            r.add(PASS, "output_dir", cfg.output_dir)
        else:
            r.add(FAIL, "output_dir vacío", "necesario para {OUTPUT_DIR} expansion")

        r.add(PASS if is_configured(cfg) else FAIL, "is_configured()", str(is_configured(cfg)))
    except Exception as e:  # noqa: BLE001
        r.add(FAIL, "config load", str(e))
        traceback.print_exc()

    # ── 4. Conectividad al server (poll endpoint) ──────────────────────
    section("4. Conectividad al server")
    try:
        from kernel_agent.config import load_config
        from kernel_agent.api_client import ApiClient, ApiError
        cfg = load_config()
        if cfg.server_url and cfg.api_key:
            with ApiClient(cfg.server_url, cfg.api_key) as client:
                resp = client.poll(
                    library_scenes=[],  # smoke: vacío para no contaminar
                )
            agent_name = resp.get("agent", {}).get("name", "?")
            has_job = resp.get("job") is not None
            r.add(PASS, "poll OK", f"agent={agent_name} · job_pendiente={has_job}")
        else:
            r.add(FAIL, "skip poll", "config incompleta")
    except ApiError as e:
        r.add(FAIL, f"poll error HTTP {e.status_code}", e.message)
    except Exception as e:  # noqa: BLE001
        r.add(FAIL, "poll exception", f"{type(e).__name__}: {e}")

    # ── 5. Library scan ────────────────────────────────────────────────
    section("5. Library scan")
    try:
        from kernel_agent.library_scan import scan_blend_files
        from kernel_agent.config import load_config
        cfg = load_config()
        scenes = scan_blend_files(cfg.library_dir)
        if scenes:
            r.add(PASS, f"scan_blend_files: {len(scenes)} scenes", json.dumps([s["name"] for s in scenes]))
        else:
            r.add(WARN, "library vacía", f"pon .blend en {cfg.library_dir}")
    except Exception as e:  # noqa: BLE001
        r.add(FAIL, "scan_blend_files", str(e))

    # ── 6. Resumen ─────────────────────────────────────────────────────
    section("Resumen")
    return r.summary()


if __name__ == "__main__":
    sys.exit(main())
