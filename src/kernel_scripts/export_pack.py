"""export_pack — genera los 29 archivos finales por proyecto.

Estructura del Export Pack (specs Coca-Cola):
    - 1 PSD Master  · 600 DPI · Layered · Tabloid (11"x17")
    - 28 PNGs (7 vistas × 4 formatos):
        * Front, Back, Lateral_L, Lateral_R, Iso_L, Iso_R, Hero_Sudada
        * 2000x2000 (sRGB, 300 DPI, transparente)
        * 1000x1000 (sRGB, 72 DPI, transparente)
        * 800x800   (sRGB, 72 DPI, transparente)
        * Thumbnail (sRGB, 72 DPI, transparente, 400x400)

Naming convention configurable por cliente. Empaqueta todo en ZIP listo
para entregar.

**Sin AI en esta capa** — código Python puro y determinista.

Pendiente implementación final en Fase 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Sin pydantic en runtime — Blender embebido no lo trae instalado y no
# necesitamos validación pesada en Fase 4 todavía. Cuando llegue Fase 4
# se puede agregar pydantic instalándolo en el Python de Blender.

@dataclass
class ExportFormat:
    name: str
    width: int
    height: int
    dpi: int
    description: str


DEFAULT_FORMATS: tuple[ExportFormat, ...] = (
    ExportFormat(name="2000", width=2000, height=2000, dpi=300, description="E-commerce alto impacto"),
    ExportFormat(name="1000", width=1000, height=1000, dpi=72, description="Catálogos web, redes"),
    ExportFormat(name="800", width=800, height=800, dpi=72, description="Thumbnails, listados"),
    ExportFormat(name="thumb", width=400, height=400, dpi=72, description="Vista previa interna"),
)


def export_pack(
    *,
    project_id: str,
    renders_dir: Path,
    output_dir: Path,
    client_id: str = "coca",
    naming_template: str = "{brand}_{flavor}_{view}_{format}.png",
    create_zip: bool = True,
) -> dict[str, Any]:
    """Genera los 29 archivos del Export Pack para un proyecto.

    Args:
        project_id: ID del proyecto en Supabase (para lookup de metadata).
        renders_dir: Carpeta con los 7 renders maestros (output de render_seven_views).
        output_dir: Carpeta destino (se crea).
        client_id: Cliente para aplicar su config (naming, formatos, etc.).
        naming_template: Template del nombre de archivo.
        create_zip: Si True, empaqueta todo en ZIP.

    Returns:
        ExportPackOutput con paths de todos los archivos generados.

    Side effects:
        Crea 29 archivos en output_dir + opcionalmente un ZIP.
    """
    if not renders_dir.exists():
        raise FileNotFoundError(f"renders_dir no existe: {renders_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    # TODO Fase 4: implementación
    # 1. Lookup metadata del proyecto en Supabase (brand, flavor, etc.)
    # 2. Lookup config del cliente en Supabase
    # 3. Para cada render en renders_dir:
    #    a. Generar las 4 versiones (2000, 1000, 800, thumb) con Pillow
    #    b. Aplicar naming convention con el template
    # 4. Generar PSD Master Tabloid 600 DPI con los renders como layers
    #    (usar psd-tools o similar)
    # 5. Si create_zip: empaquetar todo en ZIP
    # 6. Validar specs (DPI, sRGB, transparencia) antes de retornar

    raise NotImplementedError("Implementación real pendiente Fase 4")


TOOL_SCHEMA = {
    "name": "export_pack",
    "description": "Genera los 29 archivos finales del Export Pack para un proyecto (1 PSD master + 28 PNGs en 7 vistas × 4 formatos). Sin AI, código determinista.",
    "input_schema": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "renders_dir": {"type": "string"},
            "output_dir": {"type": "string"},
            "client_id": {"type": "string", "default": "coca"},
            "create_zip": {"type": "boolean", "default": True},
        },
        "required": ["project_id", "renders_dir", "output_dir"],
    },
}
