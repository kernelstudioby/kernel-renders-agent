"""set_active_view_layer — activa un view layer específico en una escena Blender.

Beyond usa convencionalmente view layers `dry` y `sweaty` para distinguir
estados de la botella (con/sin condensación). Antes de renderizar, conviene
activar el correcto.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def run_set_active_view_layer(
    *,
    scene: str,
    view_layer_name: str,
) -> dict[str, Any]:
    """Activa un view layer en una escena de Blender.

    Args:
        scene: Ruta absoluta al .blend.
        view_layer_name: Nombre exacto del view layer (case-sensitive).
            Convención Beyond: 'dry' (seco) o 'sweaty' (con condensación).

    Returns:
        dict con previous_view_layer, new_view_layer, available_view_layers.

    Raises:
        FileNotFoundError, ValueError si el view layer no existe.

    Side effects:
        Modifica `scene.window.view_layer` y guarda el .blend.
    """
    import bpy  # type: ignore[import-not-found]

    # NOTA: no abrimos ni guardamos. Escena ya cargada por el executor.

    blender_scene = bpy.context.scene
    available = [vl.name for vl in blender_scene.view_layers]

    if view_layer_name not in available:
        raise ValueError(
            f"View layer '{view_layer_name}' no existe. "
            f"Disponibles: {available}"
        )

    # 2. Capturar estado previo
    previous = None
    try:
        if bpy.context.window and bpy.context.window.view_layer:
            previous = bpy.context.window.view_layer.name
    except (AttributeError, ReferenceError):
        # En modo background bpy.context.window puede no tener view_layer
        previous = None

    target_vl = blender_scene.view_layers[view_layer_name]

    # 3. Activar el view layer.
    # En Blender 5.x view_layers es una colección sin atributo .active asignable.
    # Para renderizar solo el view layer objetivo, habilitamos use=True en él
    # y deshabilitamos los demás. Esto asegura que bpy.ops.render.render()
    # procese únicamente el target.
    disabled = []
    for vl in blender_scene.view_layers:
        if vl.name == view_layer_name:
            vl.use = True
        elif vl.use:
            vl.use = False
            disabled.append(vl.name)

    # Si hay window context (raro en --background), también lo seteamos
    try:
        if bpy.context.window:
            bpy.context.window.view_layer = target_vl
    except (AttributeError, TypeError, ReferenceError):
        pass

    # 4. Aplicar la visibilidad del view_layer a nivel de Collection Y de
    # Object. Por qué dos niveles:
    #   - collection.hide_render: scene-wide, persiste si la scene se reusa.
    #   - object.hide_render: propiedad del objeto. CRÍTICO: render_views.py
    #     a veces crea una "fresh scene" para sortear locks de file_format.
    #     En esa fresh scene los layer_collections del original no existen,
    #     y los objetos se linkean directo a la fresh scene perdiendo la
    #     visibilidad de collection. Pero obj.hide_render viaja con el
    #     objeto en el linkeo. Por eso marcamos AMBOS.
    collections_hidden = []
    collections_shown = []
    objects_hidden_count = 0
    objects_shown_count = 0

    def _walk_layer_collections(lc):
        nonlocal objects_hidden_count, objects_shown_count
        try:
            target_excluded = bool(lc.exclude)
        except AttributeError:
            target_excluded = False
        coll = lc.collection
        try:
            coll.hide_render = target_excluded
            if target_excluded:
                collections_hidden.append(coll.name)
            else:
                collections_shown.append(coll.name)
        except AttributeError:
            pass
        # Propagar a TODOS los objetos del collection (incluye nested
        # collections recursivamente vía all_objects)
        try:
            for obj in coll.all_objects:
                try:
                    obj.hide_render = target_excluded
                    if target_excluded:
                        objects_hidden_count += 1
                    else:
                        objects_shown_count += 1
                except AttributeError:
                    pass
        except AttributeError:
            pass
        for child in lc.children:
            _walk_layer_collections(child)

    try:
        root_lc = target_vl.layer_collection
        for child in root_lc.children:
            _walk_layer_collections(child)
    except AttributeError:
        pass

    # NO guardamos. Cambios solo en memoria.

    return {
        "scene": scene,
        "previous_view_layer": previous,
        "new_view_layer": view_layer_name,
        "available_view_layers": available,
        "disabled_others": disabled,
        "collections_hidden": collections_hidden,
        "collections_shown": collections_shown,
        "objects_hidden_count": objects_hidden_count,
        "objects_shown_count": objects_shown_count,
        "success": True,
    }


TOOL_SCHEMA = {
    "name": "set_active_view_layer",
    "description": "Activa un view layer específico en la escena Blender (ej. 'dry' o 'sweaty'). Útil cuando la escena tiene múltiples view layers para distintos estados del producto.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scene": {"type": "string"},
            "view_layer_name": {
                "type": "string",
                "description": "Nombre exacto del view layer, case-sensitive. Convención Beyond: 'dry' o 'sweaty'.",
            },
        },
        "required": ["scene", "view_layer_name"],
    },
}

set_active_view_layer = run_set_active_view_layer
