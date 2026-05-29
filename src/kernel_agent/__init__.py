"""Kernel Renders Agent.

Daemon que corre en una PC con GPU, hace polling a la plataforma web
(https://kernel-renders.vercel.app) y ejecuta jobs de Blender contra
escenas locales. Sube los renders a Supabase Storage.

Patrón de auth: api_key estática por agente (stilo Kernel Pack CEP).
"""

__version__ = "0.1.0"
