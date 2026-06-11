"""swap_label — reemplaza la textura de etiqueta en un material de Blender.

Implementación real con bpy. Se ejecuta dentro de un proceso Blender
(headless o GUI) que importó este módulo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def run_swap_label(
    *,
    scene: str,
    png_path: str,
    material_name: str = "Label",
) -> dict[str, Any]:
    """Ejecuta el swap dentro de Blender.

    Args:
        scene: Ruta absoluta al .blend del Asset Master.
        png_path: Ruta absoluta al PNG nuevo.
        material_name: Material a modificar. Default 'Label' (estándar Beyond).

    Returns:
        dict con info de la operación.

    Raises:
        FileNotFoundError, ValueError, RuntimeError según el caso.
    """
    import bpy  # type: ignore[import-not-found]

    png = Path(png_path)

    if not png.exists():
        raise FileNotFoundError(f"PNG no existe: {png}")

    # NOTA: no abrimos ni guardamos el .blend aquí. Asumimos que el executor
    # ya cargó la escena correcta en bpy. Esto evita sobrescribir el archivo
    # original (que puede ser de cientos de MB).

    # 1. Encontrar el material — exact match, sino fuzzy (escenas multipack
    # usan label_so/label_zero, no el genérico "Label").
    mat = bpy.data.materials.get(material_name)
    resolved_name = material_name
    if mat is None:
        resolved = _resolve_label_material(material_name, bpy)
        if resolved is None:
            raise ValueError(
                f"Material '{material_name}' no existe en la escena. "
                f"Disponibles: {[m.name for m in bpy.data.materials]}"
            )
        mat = bpy.data.materials[resolved]
        resolved_name = resolved
        print(
            f"[swap_label] '{material_name}' no existe; usando "
            f"'{resolved_name}' (fuzzy match con view_layer activo)",
            flush=True,
        )

    if not mat.use_nodes:
        raise ValueError(f"Material '{resolved_name}' no usa nodos (use_nodes=False)")

    # 3. Encontrar el nodo Image Texture conectado al Base Color del Principled BSDF
    img_node = _find_label_image_node(mat)
    if img_node is None:
        raise ValueError(
            f"No se encontró un nodo Image Texture conectado al Base Color "
            f"del Principled BSDF en '{resolved_name}'. "
            f"Asegúrate de que el material tenga la estructura esperada."
        )

    previous_filepath = img_node.image.filepath if img_node.image else None

    # 4. Cargar la nueva imagen y asignarla al nodo
    new_image = bpy.data.images.load(str(png), check_existing=False)
    new_image.colorspace_settings.name = "sRGB"
    img_node.image = new_image

    # NO guardamos. Cambios solo en memoria.

    return {
        "scene": scene,
        "material_name": resolved_name,
        "material_requested": material_name,
        "previous_texture": previous_filepath,
        "new_texture": str(png),
        "node_name": img_node.name,
        "success": True,
    }


def _resolve_label_material(requested: str, bpy: Any) -> str | None:
    """Fuzzy match para escenas multipack con materiales separados por variant.

    Caso real (multipack de Moy): la AI por default pasa material_name='Label'
    pero la escena tiene 'label_so' y 'label_zero'. Heurística:

    1. Buscar materiales cuyo nombre case-insensitive empiece con el requested
       (o con 'label' si requested es genérico). Si solo hay uno, lo usamos.
    2. Si hay varios, cruzar con el view_layer activo: el VL 'termo6pack_*_so'
       sugiere material '*_so'; el VL '*_zero' sugiere '*_zero'.
    3. Si no se puede deducir, retornamos None y el caller emite error claro.
    """
    all_mats = [m.name for m in bpy.data.materials]
    requested_lower = requested.lower()

    # Candidatos por prefijo (case-insensitive). 'Label' / 'label' matchea
    # ['label_so', 'label_zero']. 'cap' matchea ['cap_so', 'cap_zero'].
    candidates = [m for m in all_mats if m.lower().startswith(requested_lower)]
    # Si requested es exactamente "label" (genérico) y no hay matches, intenta
    # buscar cualquier material que contenga "label" en su nombre.
    if not candidates and requested_lower in ("label", "etiqueta"):
        candidates = [m for m in all_mats if "label" in m.lower()]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # Hay varios: cruzar con view_layer activo.
    try:
        active_vl = bpy.context.window.view_layer.name if bpy.context.window else None
    except (AttributeError, ReferenceError):
        active_vl = None
    if active_vl is None:
        try:
            active_vl = bpy.context.scene.view_layers.active.name
        except (AttributeError, ReferenceError):
            active_vl = None

    if active_vl:
        vl_lower = active_vl.lower()
        # Detectar variant del view_layer y buscar material con mismo sufijo
        for variant in ("zero", "so"):
            if variant in vl_lower:
                matches = [m for m in candidates if variant in m.lower()]
                if len(matches) == 1:
                    return matches[0]
                if matches:
                    # Si hay varios, devolver el primero por orden alfabético
                    return sorted(matches)[0]

    # No se pudo deducir, retornar el primero por orden alfabético como
    # last resort (mejor que fallar mostrando una lista enorme al user).
    return sorted(candidates)[0]


def _find_label_image_node(mat: Any) -> Any | None:
    """Busca el Image Texture node conectado al Base Color del Principled BSDF.

    Estrategia:
    1. Encontrar el Principled BSDF (info.id 'BSDF_PRINCIPLED')
    2. Seguir la conexión de Base Color → buscar Image Texture
    3. Si no hay link directo, fallback: cualquier Image Texture en el material
    """
    principled = next(
        (n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"),
        None,
    )
    if principled:
        base_color = principled.inputs.get("Base Color")
        if base_color and base_color.is_linked:
            link = base_color.links[0]
            from_node = link.from_node
            # Si está directo: Image Texture → Base Color
            if from_node.bl_idname == "ShaderNodeTexImage":
                return from_node
            # Si hay un nodo en medio (ej. Color Mix, Hue/Sat), buscar el Image Texture
            # más cercano en upstream
            return _find_upstream_image_node(from_node)

    # Fallback: primer Image Texture del material
    for n in mat.node_tree.nodes:
        if n.bl_idname == "ShaderNodeTexImage":
            return n

    return None


def _find_upstream_image_node(node: Any, visited: set[str] | None = None) -> Any | None:
    """Busca recursivamente upstream el primer Image Texture node."""
    if visited is None:
        visited = set()
    if node.name in visited:
        return None
    visited.add(node.name)

    if node.bl_idname == "ShaderNodeTexImage":
        return node

    for input_socket in node.inputs:
        if input_socket.is_linked:
            for link in input_socket.links:
                found = _find_upstream_image_node(link.from_node, visited)
                if found:
                    return found
    return None


# Schema JSON para tool use de Claude (espejado en apps/web/src/lib/ai/tools.ts)
TOOL_SCHEMA = {
    "name": "swap_label",
    "description": "Reemplaza la textura de etiqueta de un material en una escena de Blender. Conserva normales, UV y los demás nodos del material.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scene": {"type": "string"},
            "png_path": {"type": "string"},
            "material_name": {"type": "string", "default": "Label"},
        },
        "required": ["scene", "png_path"],
    },
}


# Alias para compat con el runner (que espera run_<tool_name>) y con el stub anterior
swap_label = run_swap_label
