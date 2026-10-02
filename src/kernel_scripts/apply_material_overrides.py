"""apply_material_overrides — cambia el material asignado a un slot de un objeto.

KER3-45: el selector "Objetos y materiales" de AI Operations manda, por cada
componente elegido, `{object, slot, material}`. Se aplica solo durante el
render: el executor ya cargó el .blend en memoria y nunca lo guarda, así que
el archivo original no cambia.
"""

from __future__ import annotations

from typing import Any


def run_apply_material_overrides(
    *,
    scene: str,
    overrides: list[dict[str, Any]],
) -> dict[str, Any]:
    """Asigna `bpy.data.materials[material]` al slot indicado de cada objeto.

    Args:
        scene: Ruta al .blend (la usa el executor para abrirlo).
        overrides: Lista de `{"object": str, "slot": int, "material": str}`.

    Returns:
        dict con `applied`: por override, el material anterior y el nuevo.
    """
    import bpy  # type: ignore[import-not-found]

    if not isinstance(overrides, list) or not overrides:
        raise ValueError("overrides debe ser una lista no vacía de {object, slot, material}")

    applied: list[dict[str, Any]] = []
    for i, ov in enumerate(overrides):
        if not isinstance(ov, dict):
            raise ValueError(f"overrides[{i}] debe ser un objeto {{object, slot, material}}")
        obj_name = ov.get("object")
        mat_name = ov.get("material")
        try:
            slot_index = int(ov.get("slot", 0))
        except (TypeError, ValueError) as e:
            raise ValueError(f"overrides[{i}].slot inválido: {ov.get('slot')!r}") from e
        if not obj_name or not mat_name:
            raise ValueError(f"overrides[{i}] requiere 'object' y 'material'")

        obj = bpy.data.objects.get(obj_name)
        if obj is None or obj.type != "MESH":
            meshes = sorted(o.name for o in bpy.data.objects if o.type == "MESH")
            raise ValueError(
                f"Objeto MESH '{obj_name}' no existe en el .blend. Disponibles: {meshes}"
            )

        slots = obj.material_slots
        if not 0 <= slot_index < len(slots):
            raise ValueError(
                f"Slot {slot_index} fuera de rango en '{obj_name}' (tiene {len(slots)} slots)"
            )

        mat = bpy.data.materials.get(mat_name)
        if mat is None:
            prefix = mat_name.split("_", 1)[0].lower()
            same_prefix = sorted(
                m.name for m in bpy.data.materials if m.name.split("_", 1)[0].lower() == prefix
            )
            raise ValueError(
                f"Material '{mat_name}' no existe. "
                f"Variantes con ese prefijo: {same_prefix or sorted(m.name for m in bpy.data.materials)}"
            )

        slot = slots[slot_index]
        previous = slot.material.name if slot.material else None
        slot.material = mat
        entry: dict[str, Any] = {
            "object": obj_name,
            "slot": slot_index,
            "previous": previous,
            "material": mat.name,
        }
        if slot.link == "DATA" and obj.data is not None and obj.data.users > 1:
            entry["shared_mesh_users"] = obj.data.users
        applied.append(entry)
        print(f"[apply_material_overrides] {obj_name}[{slot_index}]: {previous} -> {mat.name}", flush=True)

    return {"success": True, "applied": applied}
