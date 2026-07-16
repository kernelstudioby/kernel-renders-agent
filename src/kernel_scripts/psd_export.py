"""psd_export — abre un PSD (local o remoto), genera N exports en resoluciones.

Pensado para ejecutarse fuera de Blender (Python normal con psd-tools + Pillow).
El daemon lo invoca cuando ve un plan con el tool `export_psd`.

`psd_path` puede ser:
  - Una ruta local del disco del agent (ej. los PSDs del PSDS_DIR)
  - Una URL https:// (ej. signed URL de Supabase Storage para uploads del browser).
    El agent la descarga a un tempfile, procesa, y borra al terminar.

Argumentos del plan:
{
  "tool": "export_psd",
  "args": {
    "psd_path": "C:/...psd"  |  "https://...psd?token=...",
    "psd_filename": "joya_manzana.psd",  // opcional, override del nombre que
                                          // se usará como prefijo de outputs
    "output_dir": "C:/KernelRenders/output/exports/<job_id>",
    "exports": [
      {"name": "Original", "width": -1, "format": "png", "transparent": true},
      {"name": "2000", "width": 2000, "height": 2000, "format": "jpg", "dpi": 300},
      ...
    ]
  }
}

Devuelve `{"outputs": [{path, name, format, width, height, size_kb}, ...]}`
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


@contextmanager
def _resolve_psd_path(psd_path: str, filename_hint: str | None) -> Iterator[Path]:
    """Devuelve un Path local al PSD, descargando si es URL https.

    Si descargamos a tempfile, se borra al salir del contexto.
    """
    is_url = psd_path.startswith(("http://", "https://"))
    if not is_url:
        yield Path(psd_path)
        return

    # Descargar a un tempfile con el nombre real (para que psd_stem sea el
    # filename que el usuario subió, no un UUID).
    safe_name = (filename_hint or "uploaded.psd").replace("/", "_").replace("\\", "_")
    if not safe_name.lower().endswith(".psd"):
        safe_name += ".psd"
    tmp_dir = Path(tempfile.mkdtemp(prefix="kernel_psd_"))
    local_path = tmp_dir / safe_name
    try:
        import httpx
    except ImportError as e:
        raise RuntimeError(
            "httpx no instalado en el agent. Necesario para descargar PSDs remotos."
        ) from e

    print(f"[psd] Descargando PSD remoto → {local_path}", flush=True)
    total_bytes = 0
    with httpx.stream("GET", psd_path, timeout=300.0, follow_redirects=True) as r:
        r.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in r.iter_bytes(1024 * 1024):  # 1 MB chunks
                f.write(chunk)
                total_bytes += len(chunk)
    print(
        f"[psd] PSD descargado · {total_bytes // 1024} KB",
        flush=True,
    )
    try:
        yield local_path
    finally:
        # Cleanup tempfile
        try:
            local_path.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass


def run_export_psd(
    *,
    psd_path: str,
    output_dir: str,
    exports: list[dict[str, Any]],
    psd_filename: str | None = None,
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

    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    with _resolve_psd_path(psd_path, psd_filename) as psd_file:
        if not psd_file.exists():
            raise FileNotFoundError(f"PSD no existe: {psd_file}")
        return _process_psd(psd_file, out_root, exports, psd_filename, PSDImage, Image)


def _process_psd(
    psd_file: Path,
    out_root: Path,
    exports: list[dict[str, Any]],
    psd_filename_override: str | None,
    PSDImage: Any,
    Image: Any,
) -> dict[str, Any]:
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
        transparent = bool(spec.get("transparent", fmt != "jpg"))
        dpi_raw = spec.get("dpi")
        dpi = int(dpi_raw) if dpi_raw else None

        # width == -1 → mantener resolución nativa del PSD (no redimensiona)
        if width == -1:
            new_w, new_h = src_w, src_h
            final_w, final_h = src_w, src_h
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
                # Sin height explícito: deducir manteniendo proporción del PSD.
                height = round(width * src_h / src_w)
            else:
                height = int(height_raw)
                if height <= 0:
                    raise ValueError(f"height inválido en export '{name}': {height}")
            # Output final = lienzo del tamaño pedido (WxH exacto).
            # Imagen ajustada con "fit inside" del aspect del PSD, centrada
            # en el lienzo. El padding sobrante queda transparente (PNG) o
            # blanco (JPG / PNG sin transparencia). Esto es lo que la gente
            # de packaging típicamente entrega al cliente: lienzos cuadrados
            # de 2000x2000, 1080x1080 IG, etc.
            final_w, final_h = width, height
            scale = min(width / src_w, height / src_h)
            new_w = max(1, round(src_w * scale))
            new_h = max(1, round(src_h * scale))
            resized = composite.resize((new_w, new_h), Image.LANCZOS)

        # Componer el lienzo final WxH, con la imagen centrada
        if fmt == "jpg" or not transparent:
            bg_color = (255, 255, 255, 255)
        else:
            bg_color = (0, 0, 0, 0)
        canvas = Image.new("RGBA", (final_w, final_h), bg_color)
        # Centrar la imagen redimensionada en el lienzo
        offset_x = (final_w - new_w) // 2
        offset_y = (final_h - new_h) // 2
        alpha = resized.split()[3] if resized.mode == "RGBA" else None
        canvas.paste(resized, (offset_x, offset_y), mask=alpha)
        if fmt == "jpg":
            resized = canvas.convert("RGB")
        else:
            resized = canvas
        new_w, new_h = final_w, final_h

        ext = "jpg" if fmt == "jpg" else fmt
        safe_name = name.replace("/", "_").replace("\\", "_")
        # Si vino psd_filename_override (uploads del browser que necesitan
        # preservar el nombre original), úsalo para derivar el stem; sino
        # usa el nombre del file local (que puede ser un tempfile UUID).
        if psd_filename_override:
            base_for_stem = psd_filename_override
        else:
            base_for_stem = psd_file.name
        psd_stem = (
            Path(base_for_stem).stem.replace("/", "_").replace("\\", "_")
        )
        # Naming de los entregables — conserva la identidad del PSD origen:
        #   - "Original" (case-insensitive): el archivo queda con el nombre
        #     del PSD tal cual, sin sufijo. Ej: "joya_manzana.png"
        #   - Otros: psd_stem + "_" + name. Ej: "joya_manzana_2000.jpg"
        if safe_name.lower() == "original":
            full_name = psd_stem
        else:
            full_name = f"{psd_stem}_{safe_name}"
        out_path = out_root / f"{full_name}.{ext}"
        save_kwargs: dict[str, Any] = {}
        if fmt == "jpg":
            save_kwargs.update({"quality": 92, "optimize": True, "format": "JPEG"})
        elif fmt == "png":
            save_kwargs.update({"compress_level": 9, "format": "PNG"})
        elif fmt == "webp":
            save_kwargs.update({"quality": 90, "format": "WEBP"})
        if dpi:
            # Pillow escribe la metadata de DPI real en PNG (chunk pHYs) y JPEG
            # (densidad JFIF). WebP no tiene un campo de DPI estándar — Pillow
            # acepta el kwarg sin error pero no lo persiste (verificado).
            save_kwargs["dpi"] = (dpi, dpi)
        resized.save(out_path, **save_kwargs)
        size_kb = max(1, out_path.stat().st_size // 1024)
        print(
            f"[psd] {full_name}.{ext} {new_w}x{new_h} ({size_kb} KB)",
            flush=True,
        )
        outputs.append(
            {
                "path": str(out_path),
                "name": full_name,
                "format": ext,
                "width": new_w,
                "height": new_h,
                "dpi": dpi,
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
                        "dpi": {
                            "type": "integer",
                            "description": "Metadata de resolución de impresión (ej. 300). Solo se embebe en PNG/JPG — WebP no soporta DPI.",
                        },
                    },
                    "required": ["name", "width", "format"],
                },
            },
        },
        "required": ["psd_path", "output_dir", "exports"],
    },
}


export_psd = run_export_psd
