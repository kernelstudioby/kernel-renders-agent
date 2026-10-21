"""Kernel Renders Agent.

Daemon que corre en una PC con GPU, hace polling a la plataforma web
(https://kernel-renders-web.vercel.app) y ejecuta jobs de Blender contra
escenas locales. Sube los renders a Supabase Storage.

Patrón de auth: api_key estática por agente (stilo Kernel Pack CEP).
"""

from importlib.metadata import PackageNotFoundError, version

# DEV-1156: antes era un literal fijo en "0.2.2" desde agosto. Se lee de la
# metadata del paquete, que se actualiza con `pip install -e .`.
try:
    __version__ = version("kernel-renders-agent")
except PackageNotFoundError:
    __version__ = "desconocida"
