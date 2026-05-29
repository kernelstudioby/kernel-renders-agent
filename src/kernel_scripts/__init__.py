"""Kernel Scripts — librería Python determinista para Kernel Renders.

Cada función expuesta aquí es una **operación auditada** que la capa AI puede
invocar vía tool use. La IA no escribe código en vivo; selecciona de esta
librería.

Convenciones:
- Cada script tiene type hints completos y docstring detallado.
- Cada script tiene tests deterministas en `tests/`.
- Inputs y outputs validados con Pydantic donde aplique.
- Side effects documentados (qué archivos crea, qué modifica en la escena).

Catálogo Fase 0 (base):
    - swap_label: reemplaza textura de etiqueta en un material
    - set_cap_color: cambia color del material de tapa
    - render_seven_views: renderiza las 7 cámaras estándar de Coca
    - export_pack: genera los 29 archivos del Export Pack

Los scripts corren dentro de Blender vía c4dpy-equivalente o ejecutados desde
el Render Agent invocando `blender.exe --background --python script.py`.
"""

from kernel_scripts.swap_label import run_swap_label, swap_label
from kernel_scripts.set_cap_color import run_set_cap_color, set_cap_color
from kernel_scripts.set_view_layer import run_set_active_view_layer, set_active_view_layer
from kernel_scripts.inspect_scene import run_inspect_scene, inspect_scene
from kernel_scripts.render_views import (
    run_render_one_view,
    run_render_seven_views,
    run_render_all_cameras,
    run_render_rotations,
    render_one_view,
    render_seven_views,
)
from kernel_scripts.export_pack import export_pack

__version__ = "0.1.0"

__all__ = [
    # API ergonómica
    "swap_label",
    "set_cap_color",
    "set_active_view_layer",
    "render_one_view",
    "render_seven_views",
    "export_pack",
    # API internal para el executor
    "run_swap_label",
    "run_set_cap_color",
    "run_set_active_view_layer",
    "run_render_one_view",
    "run_render_seven_views",
    "run_render_all_cameras",
    "run_render_rotations",
]
