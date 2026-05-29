"""set_cap_color — cambia el Base Color del Principled BSDF de un material.

Implementación real con bpy. Convierte hex sRGB a linear RGB (lo que Blender
usa internamente para colores en shader nodes).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Acepta #RRGGBB (6 chars) y #RRGGBBAA (8 chars con alpha). Beyond usa formato
# de 8 chars en sus prompts (ej. "#000000FF" = negro opaco).
HEX_PATTERN = re.compile(r"^#?[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")


def run_set_cap_color(
    *,
    scene: str,
    rgb_hex: str,
    material_name: str = "cap",
) -> dict[str, Any]:
    """Cambia el Base Color del material de tapa.

    Args:
        scene: Ruta al .blend.
        rgb_hex: Color hex sRGB. Acepta '#RRGGBB' (6) o '#RRGGBBAA' (8).
        material_name: Material a modificar. Default 'cap' (estándar Beyond).

    Returns:
        dict con previous_color, new_color, etc.
    """
    import bpy  # type: ignore[import-not-found]

    if not HEX_PATTERN.match(rgb_hex):
        raise ValueError(
            f"rgb_hex inválido: {rgb_hex}. Formato esperado: #RRGGBB o #RRGGBBAA"
        )

    new_color_linear, alpha_linear = _hex_to_rgba_linear(rgb_hex)

    # NOTA: no abrimos ni guardamos el .blend aquí. El executor ya cargó
    # la escena. Esto evita sobrescribir el archivo original.

    # 1. Encontrar el material
    mat = bpy.data.materials.get(material_name)
    if mat is None:
        raise ValueError(
            f"Material '{material_name}' no existe. "
            f"Disponibles: {[m.name for m in bpy.data.materials]}"
        )
    if not mat.use_nodes:
        raise ValueError(f"Material '{material_name}' no usa nodos")

    # 3. Encontrar el Principled BSDF
    principled = next(
        (n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"),
        None,
    )
    if principled is None:
        raise ValueError(
            f"Material '{material_name}' no tiene un Principled BSDF. "
            f"Nodos disponibles: {[n.bl_idname for n in mat.node_tree.nodes]}"
        )

    base_color_input = principled.inputs.get("Base Color")
    if base_color_input is None:
        raise ValueError(f"Principled BSDF de '{material_name}' no tiene input 'Base Color'")

    # 4. Capturar color previo y aplicar el nuevo
    previous = tuple(base_color_input.default_value[:3])

    # Si el Base Color está conectado a otro nodo (textura, mix, etc.), avisamos
    # pero igual cambiamos el default_value — útil cuando lo desconectan después.
    was_linked = base_color_input.is_linked

    base_color_input.default_value = (*new_color_linear, alpha_linear)

    # NO guardamos. Cambios solo en memoria.

    return {
        "scene": scene,
        "material_name": material_name,
        "previous_color": previous,
        "new_color": new_color_linear,
        "rgb_hex": rgb_hex.upper() if rgb_hex.startswith("#") else f"#{rgb_hex.upper()}",
        "was_linked_warning": was_linked,
        "success": True,
    }


def _hex_to_rgba_linear(
    hex_color: str,
) -> tuple[tuple[float, float, float], float]:
    """Convierte hex sRGB → (linear RGB, alpha).

    Alpha es lineal (no se le aplica gamma). Si no se provee, default 1.0.
    Acepta '#RRGGBB' (6 chars) o '#RRGGBBAA' (8 chars).
    """
    h = hex_color.lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    alpha = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0

    def srgb_to_linear(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    rgb_linear = (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b))
    return rgb_linear, alpha


TOOL_SCHEMA = {
    "name": "set_cap_color",
    "description": "Cambia el color del material de tapa (Base Color del Principled BSDF). Acepta hex sRGB con o sin alpha: #RRGGBB o #RRGGBBAA.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scene": {"type": "string"},
            "rgb_hex": {
                "type": "string",
                "pattern": "^#?[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$",
            },
            "material_name": {"type": "string", "default": "cap"},
        },
        "required": ["scene", "rgb_hex"],
    },
}

set_cap_color = run_set_cap_color
