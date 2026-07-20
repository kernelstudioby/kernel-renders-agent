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

    # 4. Aplicar la EXCLUSIÓN del view_layer a nivel de Collection Y de
    # Object — pero SOLO en la dirección "ocultar". Por qué dos niveles:
    #   - collection.hide_render: scene-wide, persiste si la scene se reusa.
    #   - object.hide_render: propiedad del objeto. CRÍTICO: render_views.py
    #     a veces crea una "fresh scene" para sortear locks de file_format.
    #     En esa fresh scene los layer_collections del original no existen,
    #     y los objetos se linkean directo a la fresh scene perdiendo la
    #     visibilidad de collection. Pero obj.hide_render viaja con el
    #     objeto en el linkeo. Por eso marcamos AMBOS al ocultar.
    #
    # NUNCA forzamos hide_render=False (mostrar) — reportado por Moy: el
    # render "activaba" luces/objetos (ej. cap.001, la collection "lights")
    # que él había ocultado a mano en el .blend, aunque su collection
    # contenedora SÍ estuviera incluida en el view layer activo. La versión
    # anterior sincronizaba hide_render en ambas direcciones (mostrar Y
    # ocultar) según la inclusión de la collection, así que cualquier
    # ocultamiento manual del artista dentro de una collection visible se
    # perdía en cuanto se llamaba a este tool (que corre en casi todos los
    # planes, vía la regla "activar PRIMERO el view layer"). Ahora solo
    # propagamos la exclusión hacia abajo (ocultar) — lo que ya estaba
    # visible se deja intacto, y lo que el artista ocultó se queda oculto.
    collections_hidden = []
    collections_shown = []
    objects_hidden_count = 0
    objects_errored: list[str] = []

    # Tipos de objeto que NUNCA deben ocultarse por exclude de view_layer.
    # Cámaras y luces son globales a la escena — si quedan ocultas, el render
    # rompe ("no active camera") o queda oscuro. Tampoco ocultamos EMPTY
    # (parents, rigging) porque puede romper jerarquías de animación.
    _PROTECTED_OBJ_TYPES = {"CAMERA", "LIGHT", "LIGHT_PROBE", "EMPTY"}

    def _hide_obj(obj) -> None:
        """Fuerza hide_render=True con manejo robusto de cualquier excepción.

        Antes el loop era frágil: una sola excepción no-AttributeError en un
        obj rompía la iteración y los siguientes objs no se procesaban. Ese
        bug causaba que TERMO_2.5L_SIMUL.001 quedara sin ocultar en el .blend
        del multipack, y el render mostraba el shrink wrap de las dos variants
        superpuesto.

        Tampoco oculta cámaras / luces / probes / empties porque son globales
        a la escena (Moy las usa cross-view-layer y romper su visibilidad
        causaba que el render usara la cámara incorrecta).
        """
        nonlocal objects_hidden_count
        try:
            if getattr(obj, "type", None) in _PROTECTED_OBJ_TYPES:
                return
            obj.hide_render = True
            objects_hidden_count += 1
        except Exception as _e:  # noqa: BLE001
            objects_errored.append(f"{getattr(obj, 'name', '?')}: {_e}")

    def _walk_layer_collections(lc, parent_excluded: bool = False):
        try:
            this_excluded = bool(lc.exclude)
        except AttributeError:
            this_excluded = False
        # Heredar exclude del padre: si la collection padre está excluida,
        # los hijos efectivamente no rinden aunque su propio exclude sea False.
        effective_excluded = parent_excluded or this_excluded
        coll = lc.collection
        if effective_excluded:
            try:
                coll.hide_render = True
                collections_hidden.append(coll.name)
            except AttributeError:
                pass
        else:
            # Solo tracking — NO tocamos coll.hide_render aquí. Si el artista
            # la ocultó a mano (independiente de este view layer), se queda así.
            collections_shown.append(coll.name)
        # Procesar AMBOS: objetos directos del collection (objects) y los de
        # sus children (all_objects). En la práctica all_objects ya incluye
        # los directos, pero iteramos ambos defensivamente y dedupeamos por
        # id() para que un fallo en uno no se propague al otro.
        if effective_excluded:
            seen_ids: set[int] = set()
            for source in (coll.objects, coll.all_objects):
                try:
                    obj_list = list(source)
                except Exception:  # noqa: BLE001
                    continue
                for obj in obj_list:
                    if obj is None:
                        continue
                    oid = id(obj)
                    if oid in seen_ids:
                        continue
                    seen_ids.add(oid)
                    _hide_obj(obj)
        for child in lc.children:
            _walk_layer_collections(child, effective_excluded)

    try:
        root_lc = target_vl.layer_collection
        for child in root_lc.children:
            _walk_layer_collections(child, parent_excluded=False)
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
        "objects_errored": objects_errored,
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
