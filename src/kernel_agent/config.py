"""Configuración persistente del agent.

Guardada en el folder estándar del SO (usando platformdirs):
  Windows: %LOCALAPPDATA%\\KernelRendersAgent\\config.json
  Mac:     ~/Library/Application Support/KernelRendersAgent/config.json
  Linux:   ~/.config/KernelRendersAgent/config.json
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "KernelRendersAgent"


@dataclass
class AgentConfig:
    server_url: str = "https://kernel-renders.vercel.app"
    api_key: str = ""
    agent_name: str = ""
    blender_bin: str = ""
    library_dir: str = ""
    output_dir: str = ""
    poll_interval_seconds: int = 5
    # Telemetría que se manda al server en cada poll
    gpu_info: dict = field(default_factory=dict)
    blender_version: str = ""


def config_dir() -> Path:
    """Devuelve la carpeta de configuración del SO (la crea si no existe)."""
    d = Path(user_config_dir(APP_NAME))
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> AgentConfig:
    """Carga la config del disco. Si no existe, devuelve defaults vacíos.

    Si existe un archivo .env en el cwd, sus variables sobreescriben (útil
    para correr el agent en modo dev sin tocar el config global).
    """
    p = config_path()
    cfg = AgentConfig()
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)

    # Overrides desde .env / variables de entorno
    env_map = {
        "KERNEL_RENDERS_SERVER_URL": "server_url",
        "KERNEL_RENDERS_API_KEY": "api_key",
        "AGENT_NAME": "agent_name",
        "BLENDER_BIN": "blender_bin",
        "LIBRARY_DIR": "library_dir",
        "OUTPUT_DIR": "output_dir",
        "POLL_INTERVAL_SECONDS": "poll_interval_seconds",
    }
    for env_key, attr in env_map.items():
        val = os.environ.get(env_key)
        if val:
            if attr == "poll_interval_seconds":
                setattr(cfg, attr, int(val))
            else:
                setattr(cfg, attr, val)
    return cfg


def save_config(cfg: AgentConfig) -> Path:
    """Guarda config a disco. Devuelve el path donde quedó."""
    p = config_path()
    p.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    # Permisos restrictivos en Unix (api_key es sensible)
    if os.name != "nt":
        os.chmod(p, 0o600)
    return p


def is_configured(cfg: AgentConfig) -> bool:
    return bool(
        cfg.server_url
        and cfg.api_key
        and cfg.blender_bin
        and cfg.library_dir
        and cfg.output_dir
    )
