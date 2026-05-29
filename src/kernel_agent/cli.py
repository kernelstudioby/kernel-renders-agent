"""CLI principal del Kernel Renders Agent.

Comandos:
  kernel-agent setup    Wizard interactivo de configuración
  kernel-agent run      Arranca el daemon
  kernel-agent status   Muestra config + último estado
  kernel-agent doctor   Diagnóstico (Blender, GPU, conectividad)
  kernel-agent version  Muestra versión
"""

from __future__ import annotations

import logging
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Forzar UTF-8 en Windows para que Unicode/emojis funcionen
import sys as _sys
if _sys.platform == "win32":
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
        _sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from . import __version__
from .api_client import ApiClient, ApiError
from .config import AgentConfig, config_path, is_configured, load_config
from .daemon import AgentDaemon
from .gpu_detect import detect_gpu
from .setup_wizard import run_wizard

console = Console(legacy_windows=False)


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.option("--log-level", default="INFO", help="DEBUG|INFO|WARNING|ERROR")
def main(log_level: str) -> None:
    """Kernel Renders Agent — daemon que ejecuta renders Blender desde la plataforma."""
    _setup_logging(log_level)


@main.command()
def setup() -> None:
    """Configura este agent paso a paso (interactivo)."""
    try:
        run_wizard()
    except KeyboardInterrupt:
        console.print("\n[dim]Cancelado.[/dim]")
        sys.exit(1)


@main.command()
def run() -> None:
    """Arranca el daemon. Ctrl+C para detener."""
    cfg = load_config()
    if not is_configured(cfg):
        console.print(
            "[red]No hay configuración. Ejecuta primero:[/red] [bold cyan]kernel-agent setup[/bold cyan]"
        )
        sys.exit(1)

    console.print(
        Panel.fit(
            f"[bold]{cfg.agent_name}[/bold]\n"
            f"server: {cfg.server_url}\n"
            f"polling: {cfg.poll_interval_seconds}s\n"
            f"GPU: {cfg.gpu_info.get('backend', '?')} - {', '.join(cfg.gpu_info.get('devices', [])) or 'CPU'}",
            title="Kernel Renders Agent",
            border_style="green",
        )
    )

    daemon = AgentDaemon(cfg)
    try:
        daemon.run()
    except KeyboardInterrupt:
        console.print("\n[dim]Detenido por usuario.[/dim]")


@main.command()
def status() -> None:
    """Muestra config actual y estado."""
    cfg = load_config()
    if not is_configured(cfg):
        console.print(
            "[yellow]Sin configurar.[/yellow] Ejecuta [bold]kernel-agent setup[/bold]"
        )
        return

    table = Table(title="Configuración actual", show_header=False)
    table.add_column("Campo", style="cyan")
    table.add_column("Valor")
    table.add_row("Config file", str(config_path()))
    table.add_row("Agent name", cfg.agent_name or "?")
    table.add_row("Server URL", cfg.server_url)
    table.add_row("API key", cfg.api_key[:16] + "..." if cfg.api_key else "(vacío)")
    table.add_row("Blender", cfg.blender_bin)
    table.add_row("Library", cfg.library_dir)
    table.add_row("Output", cfg.output_dir)
    table.add_row("Polling", f"{cfg.poll_interval_seconds}s")
    table.add_row("GPU", cfg.gpu_info.get("backend", "?") + " · " + (", ".join(cfg.gpu_info.get("devices", [])) or "CPU"))
    table.add_row("Blender ver.", cfg.blender_version or "?")
    console.print(table)


@main.command()
def doctor() -> None:
    """Diagnostica el entorno: Blender, GPU, conectividad."""
    cfg = load_config()
    table = Table(title="Diagnóstico", show_header=True)
    table.add_column("Check")
    table.add_column("Resultado")

    # 1. Config presente
    table.add_row("Config configurada", "[green][OK][/green] Sí" if is_configured(cfg) else "[red][X][/red] No (corre 'kernel-agent setup')")

    # 2. Blender disponible
    from pathlib import Path
    blender_ok = bool(cfg.blender_bin) and Path(cfg.blender_bin).exists()
    table.add_row("Blender encontrado", f"[green][OK][/green] {cfg.blender_bin}" if blender_ok else "[red][X][/red] No")

    # 3. GPU detect
    if blender_ok:
        console.print("[dim]Detectando GPU (~30s)...[/dim]")
        gpu = detect_gpu(cfg.blender_bin)
        backend = gpu.get("backend", "CPU")
        devices = gpu.get("devices", [])
        tag = "[green][OK][/green]" if backend != "CPU" else "[yellow][!][/yellow]"
        table.add_row("GPU", f"{tag} {backend} · {', '.join(devices) or '(solo CPU)'}")
        table.add_row("Blender version", f"[green][OK][/green] {gpu.get('blender_version', '?')}")
    else:
        table.add_row("GPU", "[dim][-] Skip (sin Blender)[/dim]")

    # 4. Conectividad al servidor
    if cfg.server_url and cfg.api_key:
        try:
            with ApiClient(cfg.server_url, cfg.api_key) as client:
                resp = client.poll()
            table.add_row("Conectividad server", f"[green][OK][/green] Conectado como {resp.get('agent', {}).get('name', '?')}")
        except ApiError as e:
            table.add_row("Conectividad server", f"[red][X][/red] {e}")
    else:
        table.add_row("Conectividad server", "[dim][-] Skip (sin URL/key)[/dim]")

    console.print(table)


@main.command()
def version() -> None:
    """Muestra la versión."""
    console.print(f"Kernel Renders Agent v{__version__}")


if __name__ == "__main__":
    main()
