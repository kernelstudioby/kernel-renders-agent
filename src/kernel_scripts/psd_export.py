"""psd_export — abre un PSD local, genera N exports en distintas resoluciones.

Pensado para ejecutarse fuera de Blender (Python normal con psd-tools + Pillow).
El daemon lo invoca cuando ve un plan con el tool `export_psd`.

Argumentos del plan:
{
  "tool": "export_psd",
  "args": {
    "psd_path": "C:/Users/ceemk/PSDs/cccz_600ml_front.psd",
    "output_dir": "C:/KernelRenders/output/exports/<job_id>",
    "exports": [
      {"name": "Tabloid_600dpi", "width": 6600, "height": 5100, "format": "png", "transparent": true},
      {"name": "IG_Square_1080", "width": 1080, "height": 1080, "format": "jpg"},
      ...
    ]
  }
}

Devuelve `{"outputs": [{path, name, format, width, height, size_kb}, ...]}`
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def run_export_psd(
    *,
    psd_path: str,
    output_dir: str,
    exports: list[dict[str, Any]],
) -> dict[str, Any]:
    """Ejecuta una matriz de exports a partir de un PSD local.

    Estrategia:
      1. Abrir el PSD una sola vez con psd-tools → composite RGBA en memoria
      2. Por cada export pedido, redimensionar + convertir formato y guardar
      3. Devolver lista de paths absolutos generados

    Si psd-tools no está instalado, lanza ImportError con instrucción clara.
    """
    try:
        from psd_tools import PSDImage  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError(
            "psd-tools no instalado. Corre: pip install psd-tools Pillow"
        ) from e
    try:
        from PIL import Image  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError(
            "Pillow no instalado. Corre: pip install Pillow"
        ) from e

    psd_file = Path(psd_path)
    if not psd_file.exists():
        raise FileNotFoundError(f"PSD no existe: {psd_file}")

    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"[psd] Abriendo {psd_file.name} ({psd_file.stat().st_size // 1024} KB)", flush=True)
    psd = PSDImage.open(str(psd_file))
    composite = psd.composite()
    if composite is None:
        raise RuntimeError("PSD sin composite (¿archivo corrupto?)")
    if composite.mode != "RGBA":
        composite = composite.convert("RGBA")
    src_w, src_h = composite.size
    print(f"[psd] Composite {src_w}x{src_h} mode={composite.mode}", flush=True)

    outputs: list[dict[str, Any]] = []
    for spec in exports:
        name = str(spec.get("name", "")).strip() or "export"
        fmt = str(spec.get("format", "png")).lower()
        if fmt not in ("png", "jpg", "jpeg", "webp"):
            raise ValueError(f"Formato no soportado: {fmt}")
        if fmt == "jpeg":
            fmt = "jpg"
        width = int(spec.get("width", 0))
        # width == -1 → mantener resolución nativa del PSD (no redimensiona)
        if width == -1:
            new_w, new_h = src_w, src_h
            resized = composite
            print(
                f"[psd] {name} usa resolución original {src_w}x{src_h}",
                flush=True,
            )
        else:
            if width <= 0:
                raise ValueError(f"width inválido en export '{name}': {width}")
            height_raw = spec.get("height")
            if height_raw is None:
                height = round(width * src_h / src_w)
            else:
                height = int(height_raw)
                if height <= 0:
                    raise ValueError(f"height inválido en export '{name}': {height}")
            # Redimensionar manteniendo aspect ratio dentro de WxH (fit inside)
            scale = min(width / src_w, height / src_h)
            new_w = max(1, round(src_w * scale))
            new_h = max(1, round(src_h * scale))
            resized = composite.resize((new_w, new_h), Image.LANCZOS)
        transparent = bool(spec.get("transparent", fmt != "jpg"))

        # Aplanar si no es transparente o si JPG
        if fmt == "jpg" or not transparent:
            bg = Image.new("RGBA", (new_w, new_h), (255, 255, 255, 255))
            bg.paste(resized, mask=resized.split()[3] if resized.mode == "RGBA" else None)
            resized = bg.convert("RGB" if fmt == "jpg" else "RGBA")

        ext = "jpg" if fmt == "jpg" else fmt
        safe_name = name.replace("/", "_").replace("\\", "_")
        out_path = out_root / f"{safe_name}.{ext}"
        save_kwargs: dict[str, Any] = {}
        if fmt == "jpg":
            save_kwargs.update({"quality": 92, "optimize": True, "format": "JPEG"})
        elif fmt == "png":
            save_kwargs.update({"compress_level": 9, "format": "PNG"})
        elif fmt == "webp":
            save_kwargs.update({"quality": 90, "format": "WEBP"})
        resized.save(out_path, **save_kwargs)
        size_kb = max(1, out_path.stat().st_size // 1024)
        print(
            f"[psd] {safe_name}.{ext} {new_w}x{new_h} ({size_kb} KB)",
            flush=True,
        )
        outputs.append(
            {
                "path": str(out_path),
                "name": safe_name,
                "format": ext,
                "width": new_w,
                "height": new_h,
                "size_kb": size_kb,
            }
        )

    return {"psd": str(psd_file), "outputs": outputs, "count": len(outputs)}


# Schema JSON para tool use de la AI (espejado en apps/web/src/lib/ai/tools.ts)
TOOL_SCHEMA = {
    "name": "export_psd",
    "description": (
        "Genera N exports de un PSD local en distintas resoluciones y formatos. "
        "Útil para entregar el pack final del proyecto al cliente."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "psd_path": {"type": "string"},
            "output_dir": {"type": "string"},
            "exports": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                        "format": {"type": "string", "enum": ["png", "jpg", "webp"]},
                        "transparent": {"type": "boolean"},
                    },
                    "required": ["name", "width", "format"],
                },
            },
        },
        "required": ["psd_path", "output_dir", "exports"],
    },
}


export_psd = run_export_psd
