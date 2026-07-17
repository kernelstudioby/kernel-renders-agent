"""uv_retexture — re-etiqueta un render ya existente sin volver a rendear en
Blender, usando la técnica de "UV pass" (ST-map): un pase adicional que
Blender puede generar junto al render normal, donde cada pixel codifica a
qué coordenada de textura (u, v) le corresponde. Con eso se puede proyectar
una etiqueta nueva (2D, plana) sobre la forma ya renderizada, conservando la
distorsión/envoltura original — sin GPU, sin Cycles, en segundos.

KER — "UV Lab" (módulo experimental). Validado contra una escena real de
Beyond (cc_2.75l_scene_package.blend): el resultado de esta técnica es
visualmente casi idéntico a un re-render real con la etiqueta nueva.

Dos pasos:
  1. `_render_uv_pass_exr()` — spawnea Blender headless UNA vez, activa los
     pases UV + Material Index sobre el material de la etiqueta, y renderiza
     un EXR multilayer (32-bit, necesario — 8-bit produce artefactos).
  2. `run_uv_retexture()` — sin Blender: lee ese EXR, aísla los pixels del
     material vía el pase de Material Index, y para cada uno de esos pixels
     muestrea la etiqueta NUEVA en la coordenada (u, v) que indica el pase UV.
     El sombreado original (luces, sombras, dobleces) se preserva vía un
     "shading ratio" — cuánto más clara/oscura quedó la textura original al
     renderizarse — que se difumina (blur) antes de aplicarse para evitar que
     el detalle fino de la etiqueta VIEJA se filtre como fantasma en la nueva
     (probado — sin el blur aparece ese artefacto).

Limitaciones conocidas (documentar para el usuario en la UI):
  - Solo re-etiqueta — no cambia cámara, color de tapa, iluminación ni geometría.
  - Los reflejos/brillos ya "horneados" en el render no se recalculan; funciona
    mejor con etiquetas de acabado mate/difuso que con acabados muy brillantes.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_UV_PASS_RENDER_SCRIPT = r"""
import bpy, json, sys

material_name = {material_name!r}
camera_name = {camera_name!r}
view_layer_name = {view_layer_name!r}
resolution_x = {resolution_x!r}
resolution_y = {resolution_y!r}
samples = {samples!r}
max_edge = {max_edge!r}
exr_out = {exr_out!r}
mat_pass_index = {mat_pass_index!r}

scene = bpy.context.scene

# Igual que executor.py: los .blend de Moy suelen traer paths absolutos a su
# drive H:\ que no existen en otras máquinas. find_missing_files busca en la
# carpeta del propio .blend por nombre de archivo y relinkea lo que encuentra.
def _is_truly_missing(img):
    if img.source != 'FILE' or not img.filepath:
        return False
    if img.packed_file is not None:
        return False
    import os
    return not os.path.exists(bpy.path.abspath(img.filepath))

missing_before = [img.name for img in bpy.data.images if _is_truly_missing(img)]
if missing_before:
    print("[uv_pass render] Texturas faltantes: " + str(missing_before), flush=True)
    try:
        bpy.ops.file.find_missing_files(directory=bpy.path.abspath("//"))
    except Exception as e:
        print("[uv_pass render] find_missing_files: " + str(e), flush=True)

mat = bpy.data.materials.get(material_name)
if mat is None or not mat.use_nodes:
    print("KERNEL_UV_ERROR:material '" + material_name + "' no existe o no usa nodos")
    sys.exit(1)

tex_node = None
for n in mat.node_tree.nodes:
    if n.type == 'TEX_IMAGE':
        tex_node = n
        break
if tex_node is None or tex_node.image is None:
    print("KERNEL_UV_ERROR:no se encontro un Image Texture node con imagen en '" + material_name + "'")
    sys.exit(1)

current_tex_path = bpy.path.abspath(tex_node.image.filepath)

if view_layer_name:
    target_vl = scene.view_layers.get(view_layer_name)
    if target_vl is None:
        print("KERNEL_UV_ERROR:view_layer '" + view_layer_name + "' no existe")
        sys.exit(1)
else:
    target_vl = next((vl for vl in scene.view_layers if vl.use), scene.view_layers[0])
for vl in scene.view_layers:
    vl.use = (vl == target_vl)

target_vl.use_pass_uv = True
target_vl.use_pass_material_index = True
mat.pass_index = mat_pass_index

if camera_name:
    cam = bpy.data.objects.get(camera_name)
    if cam is None or cam.type != 'CAMERA':
        print("KERNEL_UV_ERROR:camara '" + camera_name + "' no existe")
        sys.exit(1)
    scene.camera = cam
if scene.camera is None:
    print("KERNEL_UV_ERROR:la escena no tiene camara activa")
    sys.exit(1)

if resolution_x is None and resolution_y is None and max_edge:
    # fast_preview: bajar proporcionalmente el lado mayor de la escena a
    # max_edge — solo afecta la calidad del "beauty" de fondo, no la
    # proyección UV en si.
    cur_x, cur_y = scene.render.resolution_x, scene.render.resolution_y
    scale = min(1.0, max_edge / max(cur_x, cur_y))
    resolution_x = max(1, round(cur_x * scale))
    resolution_y = max(1, round(cur_y * scale))
if resolution_x:
    scene.render.resolution_x = resolution_x
if resolution_y:
    scene.render.resolution_y = resolution_y
scene.render.resolution_percentage = 100
if samples:
    try:
        scene.cycles.samples = samples
    except AttributeError:
        pass

scene.render.image_settings.file_format = 'OPEN_EXR_MULTILAYER'
scene.render.image_settings.color_depth = '32'
scene.render.image_settings.exr_codec = 'ZIP'
scene.render.filepath = exr_out

print("KERNEL_UV_RENDER_START", flush=True)
bpy.ops.render.render(write_still=True)
print("KERNEL_UV_RENDER_OK:" + json.dumps({{
    "exr_path": exr_out,
    "current_texture_path": current_tex_path,
    "view_layer": target_vl.name,
    "camera": scene.camera.name,
}}))
"""

_TEX_NODE_PROBE_SCRIPT = r"""
import bpy, json
mat = bpy.data.materials.get({material_name!r})
out = {{"material_exists": mat is not None}}
print("KERNEL_UV_TEXPROBE:" + json.dumps(out))
"""


@contextmanager
def _resolve_local_path(path_or_url: str, filename_hint: str) -> Iterator[Path]:
    """Igual que en psd_export.py: descarga si es una URL https, sino usa el path tal cual."""
    is_url = path_or_url.startswith(("http://", "https://"))
    if not is_url:
        yield Path(path_or_url)
        return
    tmp_dir = Path(tempfile.mkdtemp(prefix="kernel_uv_"))
    safe_name = filename_hint.replace("/", "_").replace("\\", "_")
    local_path = tmp_dir / safe_name
    try:
        import httpx
    except ImportError as e:
        raise RuntimeError("httpx no instalado — necesario para descargar la etiqueta.") from e
    with httpx.stream("GET", path_or_url, timeout=120.0, follow_redirects=True) as r:
        r.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in r.iter_bytes(1024 * 1024):
                f.write(chunk)
    try:
        yield local_path
    finally:
        try:
            local_path.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass


def _render_uv_pass_exr(
    *,
    scene: str,
    blender_bin: str,
    exr_out: str,
    material_name: str,
    camera_name: str | None,
    view_layer_name: str | None,
    resolution_x: int | None,
    resolution_y: int | None,
    samples: int | None,
    max_edge: int | None,
    timeout: int,
) -> dict[str, Any]:
    script = _UV_PASS_RENDER_SCRIPT.format(
        material_name=material_name,
        camera_name=camera_name,
        view_layer_name=view_layer_name,
        resolution_x=resolution_x,
        resolution_y=resolution_y,
        samples=samples,
        max_edge=max_edge,
        exr_out=exr_out,
        mat_pass_index=1,
    )
    result = subprocess.run(
        [blender_bin, "--background", scene, "--python-expr", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    for line in result.stdout.splitlines():
        if line.startswith("KERNEL_UV_ERROR:"):
            raise RuntimeError(line[len("KERNEL_UV_ERROR:"):])
        if line.startswith("KERNEL_UV_RENDER_OK:"):
            return json.loads(line[len("KERNEL_UV_RENDER_OK:"):])
    raise RuntimeError(
        "El render de los pases UV no terminó correctamente. "
        f"stdout (últimas líneas): {result.stdout[-1500:]}\nstderr: {result.stderr[-1500:]}"
    )


_FAST_PREVIEW_SAMPLES = 128
_FAST_PREVIEW_MAX_EDGE = 1100


def run_uv_retexture(
    *,
    scene: str,
    output_path: str,
    new_label_path: str,
    blender_bin: str,
    material_name: str = "Label",
    camera_name: str | None = None,
    view_layer_name: str | None = None,
    resolution_x: int | None = None,
    resolution_y: int | None = None,
    samples: int | None = None,
    fast_preview: bool = True,
    new_label_filename: str | None = None,
    render_timeout: int = 900,
) -> dict[str, Any]:
    """Re-etiqueta un render existente vía UV pass — sin re-renderizar la escena
    completa con la etiqueta nueva.

    Args:
        scene: Ruta al .blend.
        output_path: PNG de salida.
        new_label_path: Ruta local o URL (signed URL de Storage) al PNG de la
            etiqueta nueva a proyectar.
        blender_bin: Ruta a blender.exe (este tool sí necesita Blender, pero
            corre su propio subprocess — no depende de que el executor
            genérico ya tenga la escena cargada).
        material_name: Material a re-etiquetar. Convención Beyond: 'Label'.
        camera_name, view_layer_name: Igual que render_one_view — si no se
            pasan, usa los activos del .blend.
        fast_preview: default True. El "beauty" base sobre el que se compone
            la etiqueta no necesita calidad de entrega — algunas escenas de
            Beyond vienen configuradas a 4096 samples / 4000px (confirmado:
            varios minutos por render). Con fast_preview=True, si el caller
            NO pasó resolution_x/resolution_y/samples explícitos, se bajan a
            valores de preview (~128 samples, lado mayor ≤1100px) — la
            resolución final del compuesto no cambia la calidad de la
            proyección UV, solo la del "beauty" de fondo. Pasa
            fast_preview=False (o especifica samples/resolution) para previsualizar
            a la calidad real de entrega.
        resolution_x, resolution_y, samples: Overrides opcionales del render
            base (el "beauty" sobre el que se compone la etiqueta nueva).

    Returns:
        dict con outputs, duration, y metadata de debug (mask_pixel_ratio,
        etc.) útil para diagnosticar resultados raros.
    """
    import time as _time

    import numpy as np
    import OpenEXR
    from PIL import Image, ImageFilter

    t0 = _time.time()
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    effective_samples = samples
    effective_max_edge = None
    if fast_preview:
        if samples is None:
            effective_samples = _FAST_PREVIEW_SAMPLES
        if resolution_x is None and resolution_y is None:
            effective_max_edge = _FAST_PREVIEW_MAX_EDGE

    with tempfile.TemporaryDirectory(prefix="kernel_uv_exr_") as tmp:
        exr_path = str(Path(tmp) / "uv_pass.exr")
        print(f"[uv_retexture] Renderizando pases UV + Material Index -> {exr_path}", flush=True)
        meta = _render_uv_pass_exr(
            scene=scene,
            blender_bin=blender_bin,
            exr_out=exr_path,
            material_name=material_name,
            camera_name=camera_name,
            view_layer_name=view_layer_name,
            resolution_x=resolution_x,
            resolution_y=resolution_y,
            samples=effective_samples,
            max_edge=effective_max_edge,
            timeout=render_timeout,
        )
        print(f"[uv_retexture] Render OK - view_layer={meta['view_layer']} camera={meta['camera']}", flush=True)

        exr_file = OpenEXR.File(exr_path)
        part = exr_file.parts[0]
        vl_name = meta["view_layer"]
        channels = part.channels
        combined = channels[f"{vl_name}.Combined"].pixels
        uv_u = channels[f"{vl_name}.UV.U"].pixels
        uv_v = channels[f"{vl_name}.UV.V"].pixels
        mat_idx = channels[f"{vl_name}.Material Index.X"].pixels
        H, W = uv_u.shape

        # Aislar EXACTAMENTE index==1 — otros materiales de la escena pueden
        # ya tener pass_index != 0 seteado (confirmado en escenas reales de
        # Beyond), así que un simple ">0.5" sobre-captura.
        mask = (mat_idx > 0.5) & (mat_idx < 1.5)
        mask_px = int(mask.sum())
        print(f"[uv_retexture] Pixels de '{material_name}' en el frame: {mask_px} ({100*mask_px/mask.size:.1f}%)", flush=True)
        if mask_px == 0:
            raise RuntimeError(
                f"El material '{material_name}' no es visible en esta cámara/vista — "
                "no hay pixels que re-etiquetar."
            )

        def srgb_to_linear(x: np.ndarray) -> np.ndarray:
            return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)

        def linear_to_srgb(x: np.ndarray) -> np.ndarray:
            x = np.clip(x, 0, 1)
            return np.where(x <= 0.0031308, x * 12.92, 1.055 * (x ** (1 / 2.4)) - 0.055)

        with _resolve_local_path(meta["current_texture_path"], "original_label.png") as orig_p:
            orig_tex = np.asarray(Image.open(orig_p).convert("RGB")).astype(np.float32) / 255.0
        orig_tex_linear = srgb_to_linear(orig_tex)
        otex_h, otex_w = orig_tex.shape[:2]

        with _resolve_local_path(new_label_path, new_label_filename or "new_label.png") as new_p:
            new_tex = np.asarray(Image.open(new_p).convert("RGB")).astype(np.float32) / 255.0
        new_tex_linear = srgb_to_linear(new_tex)
        ntex_h, ntex_w = new_tex.shape[:2]

        ys, xs = np.where(mask)
        u_vals = uv_u[ys, xs]
        v_vals = uv_v[ys, xs]

        # Blender: origen UV abajo-izquierda; arrays de imagen indexan desde
        # arriba — voltear V.
        otex_x = np.clip((u_vals * (otex_w - 1)).astype(np.int32), 0, otex_w - 1)
        otex_y = np.clip(((1.0 - v_vals) * (otex_h - 1)).astype(np.int32), 0, otex_h - 1)
        orig_flat_linear = orig_tex_linear[otex_y, otex_x, :]
        rendered_linear = combined[ys, xs, :3]

        # Shading ratio en luminancia (no por canal — evita bleeding de color
        # de la etiqueta vieja), difuminado (no por pixel — evita que el
        # detalle fino/texto de la etiqueta vieja se filtre como fantasma en
        # la nueva; ambos artefactos confirmados en pruebas).
        def luminance(rgb: np.ndarray) -> np.ndarray:
            return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]

        eps = 1e-3
        shade_1d = luminance(rendered_linear) / np.maximum(luminance(orig_flat_linear), eps)
        shade_full = np.ones((H, W), dtype=np.float32)
        shade_full[ys, xs] = np.clip(shade_1d, 0.0, 4.0)
        shade_img = Image.fromarray((np.clip(shade_full, 0, 2) * 127.5).astype(np.uint8))
        shade_blurred = np.asarray(shade_img.filter(ImageFilter.GaussianBlur(radius=12))).astype(np.float32) / 127.5
        shade_at_px = shade_blurred[ys, xs]

        ntex_x = np.clip((u_vals * (ntex_w - 1)).astype(np.int32), 0, ntex_w - 1)
        ntex_y = np.clip(((1.0 - v_vals) * (ntex_h - 1)).astype(np.int32), 0, ntex_h - 1)
        new_flat_linear = new_tex_linear[ntex_y, ntex_x, :]
        relit_linear = np.clip(new_flat_linear * shade_at_px[:, None], 0, 1)

        result_u8 = (linear_to_srgb(combined[..., :3]) * 255).astype(np.uint8)
        result_u8[ys, xs] = (linear_to_srgb(relit_linear) * 255).astype(np.uint8)

        Image.fromarray(result_u8).save(out_path)

    duration = _time.time() - t0
    size_kb = max(1, out_path.stat().st_size // 1024)
    print(f"[uv_retexture] {out_path.name} {W}x{H} ({size_kb} KB) en {duration:.1f}s", flush=True)
    return {
        "outputs": [
            {
                "path": str(out_path),
                "name": out_path.stem,
                "format": "png",
                "width": W,
                "height": H,
                "size_kb": size_kb,
            }
        ],
        "duration_seconds": duration,
        "mask_pixels": mask_px,
        "view_layer": meta["view_layer"],
        "camera": meta["camera"],
    }


# Schema JSON para tool use de la AI / referencia del payload esperado.
TOOL_SCHEMA = {
    "name": "uv_retexture",
    "description": (
        "Re-etiqueta un render existente proyectando una etiqueta nueva vía UV pass, "
        "sin re-renderizar la escena completa. Experimental — 'UV Lab'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scene": {"type": "string"},
            "output_path": {"type": "string"},
            "new_label_path": {"type": "string", "description": "Ruta o URL al PNG de la etiqueta nueva."},
            "material_name": {"type": "string", "description": "default: 'Label'"},
            "camera_name": {"type": "string"},
            "view_layer_name": {"type": "string"},
        },
        "required": ["scene", "output_path", "new_label_path"],
    },
}

uv_retexture = run_uv_retexture
