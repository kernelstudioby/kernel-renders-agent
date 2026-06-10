"""inspect_scene — devuelve info estructurada de la escena cargada.

Para que la AI tenga contexto antes de armar planes: qué objetos hay,
qué materiales, qué cámaras, qué view layers. Equivalente conceptual
al 'read_scene_graph' del conector oficial Anthropic-Blender, pero
para nuestro stack headless.
"""

from __future__ import annotations

from typing import Any


def run_inspect_scene(
    *,
    scene: str = "",  # ignorado, escena ya cargada por el executor
    include_polygons: bool = False,
) -> dict[str, Any]:
    """Inspecciona la escena actualmente cargada.

    Args:
        scene: parámetro ignorado (la escena ya la cargó el executor).
            Lo mantenemos para que la AI lo pase consistentemente.
        include_polygons: si True, cuenta polígonos por mesh (más lento).

    Returns:
        dict con scenes, objects, materials, cameras, view_layers,
        lights, images, dimensions, render_engine.
    """
    import bpy  # type: ignore[import-not-found]

    s = bpy.context.scene

    # Objetos por tipo
    objects_by_type: dict[str, list[str]] = {}
    for obj in s.objects:
        objects_by_type.setdefault(obj.type, []).append(obj.name)

    # Cámaras
    cameras = [o.name for o in s.objects if o.type == "CAMERA"]
    active_camera = s.camera.name if s.camera else None

    # Materiales
    materials_info = []
    for mat in bpy.data.materials:
        info = {"name": mat.name, "use_nodes": mat.use_nodes}
        if mat.use_nodes and mat.node_tree:
            # Detectar nodos clave para tool use posterior
            has_principled = any(
                n.bl_idname == "ShaderNodeBsdfPrincipled" for n in mat.node_tree.nodes
            )
            has_image_texture = any(
                n.bl_idname == "ShaderNodeTexImage" for n in mat.node_tree.nodes
            )
            info["has_principled_bsdf"] = has_principled
            info["has_image_texture"] = has_image_texture
        materials_info.append(info)

    # View layers — info detallada para entender por qué difieren entre sí
    # (dry vs sweaty, etc). Reporta exclude/holdout/indirect_only por
    # layer_collection, material_override, samples, y cualquier override
    # que pueda explicar diferencias visibles entre view_layers.
    view_layers = []
    for vl in s.view_layers:
        # Recorrer el árbol de layer_collections del view_layer
        excluded_collections: list[str] = []
        hidden_collections: list[str] = []
        holdout_collections: list[str] = []
        indirect_only_collections: list[str] = []

        def _walk(lc):
            try:
                if getattr(lc, "exclude", False):
                    excluded_collections.append(lc.collection.name)
                if getattr(lc, "hide_viewport", False):
                    hidden_collections.append(lc.collection.name)
                if getattr(lc, "holdout", False):
                    holdout_collections.append(lc.collection.name)
                if getattr(lc, "indirect_only", False):
                    indirect_only_collections.append(lc.collection.name)
            except AttributeError:
                pass
            for child in lc.children:
                _walk(child)

        try:
            for child in vl.layer_collection.children:
                _walk(child)
        except AttributeError:
            pass

        # Material override (Blender 3.x+: vl.material_override)
        mat_override = None
        try:
            if vl.material_override:
                mat_override = vl.material_override.name
        except AttributeError:
            pass

        # Cycles overrides per view_layer
        cycles_samples_override = None
        try:
            if vl.cycles.use_layer_samples != "USE":
                cycles_samples_override = vl.cycles.samples
        except AttributeError:
            pass

        # Objects hidden_render globalmente (no por view_layer pero útil saber
        # qué hay marcado para no renderizar). Solo nombres distintos por
        # view_layer son útiles, pero como bpy no expone per-view_layer hide_render
        # directamente, listamos los globales.
        objects_hidden_render = [
            o.name for o in s.objects if getattr(o, "hide_render", False)
        ][:30]

        view_layers.append({
            "name": vl.name,
            "use": vl.use,
            "excluded_collections": excluded_collections,
            "hidden_collections": hidden_collections,
            "holdout_collections": holdout_collections,
            "indirect_only_collections": indirect_only_collections,
            "material_override": mat_override,
            "cycles_samples_override": cycles_samples_override,
            "objects_hidden_render_global": objects_hidden_render,
        })

    # Lights
    lights = [
        {"name": o.name, "type": o.data.type}
        for o in s.objects
        if o.type == "LIGHT"
    ]

    # Imágenes (texturas cargadas)
    images = []
    for img in bpy.data.images:
        if img.name in ("Render Result", "Viewer Node"):
            continue
        images.append({
            "name": img.name,
            "source": img.source,
            "filepath": img.filepath if img.filepath else None,
            "size": list(img.size) if img.has_data else None,
            "has_data": img.has_data,
        })

    # Polígonos (opcional, lento en escenas grandes)
    total_polygons = None
    if include_polygons:
        total_polygons = sum(
            len(o.data.polygons) for o in s.objects if o.type == "MESH" and o.data
        )

    # Resolución actual
    resolution = (s.render.resolution_x, s.render.resolution_y)

    result = {
        "scene_name": s.name,
        "render_engine": s.render.engine,
        "resolution": list(resolution),
        "active_camera": active_camera,
        "cameras": cameras,
        "objects_count": len(s.objects),
        "objects_by_type": {
            t: len(names) for t, names in objects_by_type.items()
        },
        "meshes": [
            o.name for o in s.objects if o.type == "MESH"
        ][:50],  # limitar para no inflar el response
        "materials": materials_info,
        "view_layers": view_layers,
        "lights": lights,
        "images_count": len(images),
        "images": images[:30],  # limitar
        "total_polygons": total_polygons,
        "success": True,
    }

    # Dump del JSON a stdout (capturado por el daemon) Y a un archivo en
    # Downloads del usuario para que sea fácil de abrir y copiar.
    import json as _json
    from pathlib import Path as _Path
    print("\n[inspect_scene][BEGIN_JSON]", flush=True)
    print(_json.dumps(result, indent=2, default=str), flush=True)
    print("[inspect_scene][END_JSON]\n", flush=True)

    try:
        downloads = _Path.home() / "Downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        out_file = downloads / "kernel-inspect.json"
        out_file.write_text(_json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"[inspect_scene] JSON escrito a: {out_file}", flush=True)
    except Exception as _e:  # noqa: BLE001
        print(f"[inspect_scene] no se pudo escribir a Downloads: {_e}", flush=True)

    return result


TOOL_SCHEMA = {
    "name": "inspect_scene",
    "description": "Inspecciona la escena cargada y devuelve estructura: objetos, materiales, cámaras, view layers, luces, imágenes/texturas. Usar al inicio cuando no conoces la escena para saber qué nombres de material/cámara hay disponibles.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scene": {"type": "string", "description": "Path al .blend (ya cargado, requerido por convención)"},
            "include_polygons": {
                "type": "boolean",
                "description": "default false — true cuenta polígonos pero es más lento",
            },
        },
        "required": ["scene"],
    },
}

inspect_scene = run_inspect_scene
