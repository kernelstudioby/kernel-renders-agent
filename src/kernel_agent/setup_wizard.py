"""Setup wizard interactivo. Se invoca con `kernel-agent setup`.

Pregunta paso a paso:
  1. URL del servidor
  2. API key (validada contra el server)
  3. Path a blender.exe (autodetect en Windows)
  4. Path al library de .blend
  5. Path al output_dir (donde guardar renders)
  6. Nombre humano del agent
  7. Detecta GPU automáticamente

Al final guarda config.json en la carpeta estándar del SO.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .api_client import ApiClient, ApiError
from .config import AgentConfig, config_path, is_configured, load_config, save_config
from .gpu_detect import detect_gpu

console = Console(legacy_windows=False)


def _default_blender_path() -> str:
    """Autodetect del path a blender.exe en este SO."""
    if platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender\blender.exe",
        ]
    elif platform.system() == "Darwin":
        candidates = ["/Applications/Blender.app/Contents/MacOS/Blender"]
    else:
        candidates = ["/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender"]
    for c in candidates:
        if Path(c).exists():
            return c
    return ""


def _default_library_dir() -> str:
    home = Path.home()
    candidates = [
        home / "kernel-renders" / "recursos",
        home / "Documents" / "kernel-renders" / "library",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return str(home / "kernel-renders" / "library")


def _default_output_dir() -> str:
    home = Path.home()
    return str(home / "Downloads" / "kernel-renders")


def run_wizard() -> AgentConfig:
    """Ejecuta el wizard. Retorna la config guardada."""
    existing = load_config()
    if is_configured(existing):
        console.print(
            Panel(
                f"Ya hay configuración en [bold]{config_path()}[/bold].\n"
                f"Server: {existing.server_url}\n"
                f"Agent: {existing.agent_name or '(sin nombre)'}",
                title="⚠️  Reconfigurando",
                border_style="yellow",
            )
        )
        if not Confirm.ask("¿Quieres sobreescribirla?", default=False):
            console.print("[dim]Cancelado.[/dim]")
            return existing

    console.print(
        Panel.fit(
            "[bold]Bienvenido al Setup de Kernel Renders Agent[/bold]\n\n"
            "Vamos a configurar esta PC para que ejecute renders de la plataforma.\n"
            "Necesitas: API key del agent (copiala de [bold]/settings/agents[/bold] en la UI web).",
            border_style="cyan",
        )
    )

    cfg = AgentConfig()

    # 1. Server URL
    cfg.server_url = Prompt.ask(
        "URL del servidor",
        default=existing.server_url or "https://kernel-renders-web.vercel.app",
    ).rstrip("/")

    # 2. API key
    while True:
        cfg.api_key = Prompt.ask("API key del agent ([dim]empieza con kr_agent_[/dim])", password=False)
        if not cfg.api_key.startswith("kr_agent_"):
            console.print("[red]Formato inválido. Debe empezar con 'kr_agent_'.[/red]")
            continue
        # Probar contra el server
        try:
            with ApiClient(cfg.server_url, cfg.api_key) as client:
                resp = client.poll()
            agent_name = resp.get("agent", {}).get("name", "(sin nombre)")
            console.print(f"[green][OK][/green] Conectado como [bold]{agent_name}[/bold]")
            cfg.agent_name = agent_name
            break
        except ApiError as e:
            console.print(f"[red][X] Fallo: {e}[/red]")
            if not Confirm.ask("¿Intentar de nuevo?", default=True):
                raise SystemExit(1)

    # 3. Blender bin
    default_blender = _default_blender_path()
    while True:
        cfg.blender_bin = Prompt.ask(
            "Ruta a blender.exe",
            default=default_blender or "",
        )
        if Path(cfg.blender_bin).exists():
            break
        console.print(f"[red]No existe: {cfg.blender_bin}[/red]")

    # 4. Library
    cfg.library_dir = Prompt.ask(
        "Carpeta del library (.blend de Beyond)",
        default=_default_library_dir(),
    )
    Path(cfg.library_dir).mkdir(parents=True, exist_ok=True)

    # 5. Output
    cfg.output_dir = Prompt.ask(
        "Carpeta donde guardar renders",
        default=_default_output_dir(),
    )
    Path(cfg.output_dir).mkdir(parents=True, exist_ok=True)

    # 6. Polling
    cfg.poll_interval_seconds = int(
        Prompt.ask("Intervalo de polling (segundos)", default=str(existing.poll_interval_seconds or 5))
    )

    # 7. GPU detect
    console.print()
    console.print("[dim]Detectando GPU (puede tardar ~30s, abrir Blender headless)...[/dim]")
    gpu = detect_gpu(cfg.blender_bin)
    cfg.gpu_info = gpu
    cfg.blender_version = gpu.get("blender_version", "")

    # Resumen
    table = Table(title="Configuración", show_header=False)
    table.add_column("Campo", style="cyan")
    table.add_column("Valor")
    table.add_row("Server", cfg.server_url)
    table.add_row("Agent", cfg.agent_name)
    table.add_row("Blender", cfg.blender_bin)
    table.add_row("Library", cfg.library_dir)
    table.add_row("Output", cfg.output_dir)
    table.add_row("Polling", f"{cfg.poll_interval_seconds}s")
    table.add_row("GPU backend", gpu.get("backend", "?"))
    table.add_row("GPU devices", ", ".join(gpu.get("devices", [])) or "(ninguna)")
    table.add_row("Blender ver.", cfg.blender_version or "?")
    console.print(table)

    if not Confirm.ask("¿Guardar configuración?", default=True):
        console.print("[dim]Cancelado, no se guardó.[/dim]")
        return cfg

    p = save_config(cfg)
    console.print(f"[green][OK] Config guardado en {p}[/green]")
    console.print()
    console.print("Para arrancar el daemon: [bold cyan]kernel-agent run[/bold cyan]")
    return cfg
