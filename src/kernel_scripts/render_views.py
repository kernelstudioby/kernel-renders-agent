"""render_views — renderiza cámaras de una escena Blender.

Para Fase 1.5 PoC: una sola cámara a la vez (`run_render_one_view`).
Las 7 vistas estándar (`run_render_seven_views`) llegan en Fase 3 cuando
Moy aporte una escena real con las 7 cámaras nombradas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


_FORMAT_ALIASES = {
    "PNG": "PNG",
    "EXR": "OPEN_EXR",
    "OPEN_EXR": "OPEN_EXR",
    "EXR_MULTILAYER": "OPEN_EXR_MULTILAYER",
    "OPEN_EXR_MULTILAYER": "OPEN_EXR_MULTILAYER",
}
_FORMAT_EXT = {
    "PNG": ".png",
    "OPEN_EXR": ".exr",
    "OPEN_EXR_MULTILAYER": ".exr",
}


def run_render_one_view(
    *,
    scene: str,
    output_path: str,
    camera_name: str | None = None,
    engine: str | None = None,
    samples: int | None = None,
    resolution_x: int | None = None,
    resolution_y: int | None = None,
    output_format: str = "PNG",
) -> dict[str, Any]:
    """Renderiza UNA cámara de la escena.

    Por default RESPETA la configuración del .blend (engine, samples,
    resolución, cámara). Los args son overrides opcionales — el caso
    típico es pasar solo `scene` + `output_path` y dejar que la escena
    se renderice como la armó el 3D artist.

    Args:
        scene: Ruta al .blend.
        output_path: Ruta absoluta de salida.
        camera_name: Override de cámara. Si None, usa la activa del .blend.
        engine: Override de engine ('CYCLES', 'BLENDER_EEVEE'). Si None, respeta el .blend.
        samples: Override de samples. Si None, respeta el .blend.
        resolution_x, resolution_y: Override de resolución. Si None, respeta el .blend.
        output_format: 'PNG' (default), 'EXR' (single layer), 'EXR_MULTILAYER'
            (todos los passes). Para EXR Beyond/Moy lo usa en post-prod.

    Returns:
        dict con output_path, duration, success.
    """
    import time as _time

    import bpy  # type: ignore[import-not-found]

    normalized_format = _FORMAT_ALIASES.get(output_format.upper())
    if normalized_format is None:
        raise ValueError(
            f"output_format '{output_format}' no soportado. "
            f"Opciones: {list(_FORMAT_ALIASES.keys())}"
        )
    expected_ext = _FORMAT_EXT[normalized_format]
    output = Path(output_path)
    if output.suffix.lower() != expected_ext:
        # Reescribir la extensión para que case con el formato.
        output = output.with_suffix(expected_ext)
        print(
            f"[render setup] output_path extension reescrita a {expected_ext} → {output}",
            flush=True,
        )
    output.parent.mkdir(parents=True, exist_ok=True)

    # NOTA: no abrimos el .blend. Escena ya cargada por el executor.
    # 1. Seleccionar cámara — respetar la activa del .blend salvo override.
    blender_scene = bpy.context.scene
    all_cams = [o for o in bpy.data.objects if o.type == "CAMERA"]
    print(
        f"[render setup] Cámaras en escena: {[c.name for c in all_cams]}",
        flush=True,
    )
    print(
        f"[render setup] scene.camera (activa del .blend): "
        f"{blender_scene.camera.name if blender_scene.camera else 'NONE'}",
        flush=True,
    )
    if camera_name:
        cam = bpy.data.objects.get(camera_name)
        if cam is None:
            raise ValueError(
                f"Cámara '{camera_name}' no existe. "
                f"Cámaras: {[c.name for c in all_cams]}"
            )
        if cam.type != "CAMERA":
            raise ValueError(f"Objeto '{camera_name}' no es una cámara (es '{cam.type}')")
        blender_scene.camera = cam
        print(f"[render setup] OVERRIDE: usando cámara '{camera_name}'", flush=True)
    elif blender_scene.camera is None:
        # La escena no tiene activa; tomar la primera disponible
        first_cam = next((o for o in bpy.data.objects if o.type == "CAMERA"), None)
        if first_cam is None:
            raise ValueError("La escena no tiene ninguna cámara")
        blender_scene.camera = first_cam

    # 2. Engine: override opcional, default respeta el .blend
    if engine is not None:
        engine_aliases = {
            "BLENDER_EEVEE_NEXT": "BLENDER_EEVEE",
            "EEVEE": "BLENDER_EEVEE",
            "EEVEE_NEXT": "BLENDER_EEVEE",
        }
        normalized_engine = engine_aliases.get(engine, engine)
        try:
            blender_scene.render.engine = normalized_engine
            print(f"[render setup] engine override={normalized_engine}", flush=True)
        except TypeError as e:
            raise ValueError(f"engine '{normalized_engine}' no disponible: {e}")
    else:
        normalized_engine = blender_scene.render.engine
        print(f"[render setup] engine de la escena={normalized_engine}", flush=True)

    # 3. Resolución: override opcional
    if resolution_x is not None:
        blender_scene.render.resolution_x = resolution_x
    if resolution_y is not None:
        blender_scene.render.resolution_y = resolution_y
    print(
        f"[render setup] resolución={blender_scene.render.resolution_x}×{blender_scene.render.resolution_y} "
        f"(percentage={blender_scene.render.resolution_percentage})",
        flush=True,
    )

    engine = normalized_engine  # para el dict de retorno

    # ===== RAMA EXR =====
    # Cuando el usuario pide EXR (single o multilayer), saltamos toda la
    # lógica de workaround para PNG y respetamos la escena tal cual.
    if normalized_format in ("OPEN_EXR", "OPEN_EXR_MULTILAYER"):
        img_settings = blender_scene.render.image_settings
        img_settings.file_format = normalized_format
        try:
            img_settings.color_depth = "32"  # EXR usa float HDR
        except TypeError:
            pass
        # EXR soporta RGBA por default; si la escena lo bloquea, RGB.
        for mode in ("RGBA", "RGB"):
            try:
                img_settings.color_mode = mode
                break
            except TypeError:
                continue

        # Deshabilitar workflows que dejan threads/async pendientes después
        # de bpy.ops.render.render() y bloquean el exit de Blender (síntoma:
        # el .exr aparece en disco pero el proceso no termina). El compositor
        # con denoise nodes y el multiview son los culpables más comunes.
        for attr, val in (
            ("use_multiview", False),
            ("use_compositing", False),
        ):
            try:
                setattr(blender_scene.render, attr, val)
            except AttributeError:
                pass
        try:
            blender_scene.use_nodes = False
        except AttributeError:
            pass
        try:
            for v in blender_scene.render.views:
                v.use = v.name in ("left", "")
        except AttributeError:
            pass

        # Samples: override opcional, default respeta el .blend
        if samples is not None:
            if normalized_engine == "CYCLES":
                blender_scene.cycles.samples = samples
            elif normalized_engine == "BLENDER_EEVEE":
                try:
                    blender_scene.eevee.taa_render_samples = samples
                except AttributeError:
                    pass
        try:
            blender_scene.render.film_transparent = True
        except AttributeError:
            pass

        blender_scene.render.filepath = str(output)
        print(
            f"[render EXR] file_format={normalized_format} → {output}",
            flush=True,
        )
        start_ts = _time.time()
        bpy.ops.render.render(write_still=True)
        duration_ts = _time.time() - start_ts

        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError(f"EXR render terminó pero archivo inválido: {output}")
        print(
            f"[render EXR] OK ({output.stat().st_size // 1024} KB)",
            flush=True,
        )
        return {
            "output_path": str(output),
            "output_format": normalized_format,
            "camera": blender_scene.camera.name if blender_scene.camera else None,
            "engine": engine,
            "samples": samples,
            "resolution": (resolution_x, resolution_y),
            "duration_seconds": round(duration_ts, 2),
            "file_size_bytes": output.stat().st_size,
            "success": True,
        }

    # ===== RAMA PNG (default — incluye workaround si la escena lockea EXR_MULTILAYER) =====
    # Deshabilitar todo lo que restrinja file formats. Múltiples settings
    # pueden forzar OPEN_EXR_MULTILAYER (común en escenas con multiview
    # tipo Coca o pipelines con compositor multi-pass).
    print(
        f"[render setup] use_multiview={blender_scene.render.use_multiview}, "
        f"views_format={getattr(blender_scene.render, 'views_format', 'n/a')}, "
        f"use_compositing={getattr(blender_scene.render, 'use_compositing', 'n/a')}, "
        f"use_nodes={blender_scene.use_nodes}",
        flush=True,
    )

    try:
        blender_scene.render.use_multiview = False
    except AttributeError:
        pass
    # views_format puede ser "STEREO_3D" o "MULTIVIEW". MULTIVIEW fuerza
    # EXR multilayer. Cambiar a STEREO_3D libera otros formatos.
    try:
        blender_scene.render.views_format = "STEREO_3D"
    except (AttributeError, TypeError):
        pass
    try:
        blender_scene.render.use_compositing = False
    except AttributeError:
        pass
    try:
        blender_scene.use_nodes = False
    except AttributeError:
        pass
    # Deshabilitar todas las views excepto la default
    try:
        for view in blender_scene.render.views:
            view.use = view.name in ("left", "")
    except AttributeError:
        pass
    # Resolution percentage solo se baja si NO hubo override explícito de
    # resolución (asumimos que si el usuario pasó resolution_x, ya viene
    # con la resolución que quiere y debe ir al 100%).
    if resolution_x is not None or resolution_y is not None:
        blender_scene.render.resolution_percentage = 100

    # Intentar configurar PNG. Si la escena sigue restringiendo (algunos
    # .blend tienen settings persistentes raros), usamos EXR y convertimos
    # después con bpy.data.images (no requiere Pillow).
    img_settings = blender_scene.render.image_settings
    needs_conversion = False
    intermediate_path: Path | None = None

    # Deshabilitar TODAS las render views excepto la primera (multi-view
    # stereo fuerza EXR_MULTILAYER incluso con use_multiview=False).
    try:
        views = list(blender_scene.render.views)
        for v in views[1:]:
            v.use = False
    except AttributeError:
        pass

    # Deshabilitar render passes + denoising_store_passes + AOVs.
    # Cuando un view_layer tiene passes adicionales activos (Cryptomatte,
    # Mist, AO, denoising data, etc.), Blender bloquea el file_format a
    # OPEN_EXR_MULTILAYER. Para PNG necesitamos SOLO el pass Combined.
    passes_disabled = 0
    for vl in blender_scene.view_layers:
        if not vl.use:
            continue
        for attr in dir(vl):
            if not attr.startswith("use_pass_"):
                continue
            if attr == "use_pass_combined":
                continue
            try:
                if getattr(vl, attr):
                    setattr(vl, attr, False)
                    passes_disabled += 1
            except (AttributeError, TypeError):
                continue
        # Cryptomatte + denoising data passes viven en sub-namespaces
        for engine_ns in ("cycles", "eevee"):
            ns = getattr(vl, engine_ns, None)
            if ns is None:
                continue
            for attr in dir(ns):
                is_pass = attr.startswith("use_pass_")
                is_denoise = "denois" in attr.lower()
                is_crypto = "cryptomatte" in attr.lower()
                if not (is_pass or is_denoise or is_crypto):
                    continue
                if attr == "use_pass_combined":
                    continue
                try:
                    val = getattr(ns, attr)
                    if isinstance(val, bool) and val:
                        setattr(ns, attr, False)
                        passes_disabled += 1
                except (AttributeError, TypeError):
                    continue
        # AOVs definidos en el view_layer también fuerzan EXR_MULTILAYER
        try:
            while len(vl.aovs) > 0:
                vl.aovs.remove(vl.aovs[0])
                passes_disabled += 1
        except (AttributeError, RuntimeError):
            pass
    if passes_disabled:
        print(f"[render setup] Deshabilitados {passes_disabled} passes/AOVs/denoise (forzaban EXR_MULTILAYER)", flush=True)

    # Reset color_mode primero porque algunas escenas tienen settings
    # transitivos (ej. color_depth=32 forzado por EXR previo) que invalidan
    # PNG hasta que el color_mode se limpia.
    for mode in ("RGBA", "RGB", "BW"):
        try:
            img_settings.color_mode = mode
            break
        except TypeError:
            continue

    # Diagnóstico: print props relevantes
    print(
        f"[render setup] image_settings actual: file_format={img_settings.file_format}, "
        f"color_depth={getattr(img_settings, 'color_depth', 'n/a')}, "
        f"color_mode={getattr(img_settings, 'color_mode', 'n/a')}",
        flush=True,
    )
    print(
        f"[render setup] scene flags: use_stamp={blender_scene.render.use_stamp}, "
        f"use_file_extension={blender_scene.render.use_file_extension}, "
        f"use_render_cache={getattr(blender_scene.render, 'use_render_cache', 'n/a')}",
        flush=True,
    )

    png_ok = False
    try:
        img_settings.file_format = "PNG"
        png_ok = True
        for mode in ("RGBA", "RGB"):
            try:
                img_settings.color_mode = mode
                break
            except TypeError:
                continue
    except TypeError as png_err:
        print(f"[render setup] file_format=PNG bloqueado en scene original: {png_err}", flush=True)
        print("[render setup] Cambiando a strategy: render-en-scene-fresh con LINK_OBJECTS", flush=True)
        # Nueva scene from scratch — tiene image_settings vírgenes (acepta PNG)
        # Linkamos los objetos de la scene original a la nueva. World, engine y
        # camera se copian explícitamente.
        original_scene = blender_scene
        original_camera = original_scene.camera

        fresh_render_scene = bpy.data.scenes.new("kernel_render_scene")
        # Copiar render settings — respeta la config de la escena original
        fresh_render_scene.render.engine = normalized_engine
        fresh_render_scene.render.resolution_x = original_scene.render.resolution_x
        fresh_render_scene.render.resolution_y = original_scene.render.resolution_y
        fresh_render_scene.render.resolution_percentage = original_scene.render.resolution_percentage
        # CRÍTICO: heredar cycles.device = GPU si la original lo tiene. La fresh
        # scene se crea con defaults (CPU); sin esto Cycles renderiza en CPU
        # silenciosamente aunque OptiX esté habilitado globalmente.
        if normalized_engine == "CYCLES":
            try:
                fresh_render_scene.cycles.device = original_scene.cycles.device
                print(
                    f"[render fresh] cycles.device heredado={fresh_render_scene.cycles.device}",
                    flush=True,
                )
            except AttributeError:
                pass
        fresh_render_scene.render.image_settings.file_format = "PNG"
        try:
            fresh_render_scene.render.image_settings.color_mode = "RGBA"
        except TypeError:
            fresh_render_scene.render.image_settings.color_mode = "RGB"
        try:
            fresh_render_scene.render.image_settings.color_depth = "8"
        except TypeError:
            pass
        try:
            fresh_render_scene.render.film_transparent = original_scene.render.film_transparent
        except AttributeError:
            pass

        # Copiar color management (CRÍTICO: si no se copia, el render sale
        # con view_transform=Standard default y los colores cambian respecto
        # al .blend original que usaba AgX/Filmic/etc).
        for attr in ("view_transform", "look", "exposure", "gamma", "use_curve_mapping"):
            try:
                setattr(
                    fresh_render_scene.view_settings,
                    attr,
                    getattr(original_scene.view_settings, attr),
                )
            except (AttributeError, TypeError):
                pass
        try:
            fresh_render_scene.display_settings.display_device = (
                original_scene.display_settings.display_device
            )
        except (AttributeError, TypeError):
            pass
        # Sequencer color space (a veces afecta al output final)
        try:
            fresh_render_scene.sequencer_colorspace_settings.name = (
                original_scene.sequencer_colorspace_settings.name
            )
        except (AttributeError, TypeError):
            pass
        print(
            f"[render fresh] view_transform={fresh_render_scene.view_settings.view_transform} "
            f"look={fresh_render_scene.view_settings.look} "
            f"display={fresh_render_scene.display_settings.display_device}",
            flush=True,
        )

        # World (HDRI/environment)
        if original_scene.world:
            fresh_render_scene.world = original_scene.world
        # Linkear objetos visibles a la collection raíz
        target_collection = fresh_render_scene.collection
        for obj in original_scene.objects:
            try:
                target_collection.objects.link(obj)
            except RuntimeError:
                # ya linkeado o conflicto, ignorar
                pass
        # Camera
        if original_camera is not None:
            fresh_render_scene.camera = original_camera
        # Samples: si hay override, aplicarlo; si no, copiar de la escena original
        if normalized_engine == "CYCLES":
            try:
                fresh_render_scene.cycles.samples = (
                    samples if samples is not None else original_scene.cycles.samples
                )
            except AttributeError:
                pass
        elif normalized_engine == "BLENDER_EEVEE":
            try:
                fresh_render_scene.eevee.taa_render_samples = (
                    samples if samples is not None else original_scene.eevee.taa_render_samples
                )
            except AttributeError:
                pass

        # Switch context y render
        if bpy.context.window:
            bpy.context.window.scene = fresh_render_scene
        fresh_render_scene.render.filepath = str(output)
        start_ts = _time.time()
        bpy.ops.render.render(write_still=True)
        duration_override = _time.time() - start_ts

        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError(
                f"Render en scene fresh terminó pero PNG inválido: {output}"
            )
        print(
            f"[render] PNG escrito vía scene-fresh: {output} ({output.stat().st_size // 1024} KB)",
            flush=True,
        )

        # Cleanup
        try:
            bpy.data.scenes.remove(fresh_render_scene)
        except Exception:
            pass

        return {
            "output_path": str(output),
            "camera": original_camera.name if original_camera else None,
            "engine": engine,
            "samples": samples,
            "resolution": (resolution_x, resolution_y),
            "duration_seconds": round(duration_override, 2),
            "file_size_bytes": output.stat().st_size,
            "strategy": "fresh_scene_link_objects",
            "success": True,
        }

    if not png_ok:
        # La escena sigue restringida — renderizar a lo que sí funcione
        available_formats = [
            item.identifier
            for item in img_settings.bl_rna.properties["file_format"].enum_items
        ]
        print(
            f"[render setup] PNG bloqueado por la escena. "
            f"Formats disponibles: {available_formats}. "
            f"Renderizando a formato disponible y convertiremos a PNG después.",
            flush=True,
        )
        # Preferir formatos progresivamente: EXR, JPEG, BMP
        chosen = None
        for fmt in ("OPEN_EXR", "OPEN_EXR_MULTILAYER", "JPEG", "TIFF", "BMP"):
            if fmt in available_formats:
                try:
                    img_settings.file_format = fmt
                    chosen = fmt
                    break
                except TypeError:
                    continue
        if chosen is None:
            raise RuntimeError(
                f"No se pudo asignar ningún file_format. Disponibles: {available_formats}"
            )
        needs_conversion = True
        # Path intermedio con la extensión correcta
        ext_by_format = {
            "OPEN_EXR": ".exr",
            "OPEN_EXR_MULTILAYER": ".exr",
            "JPEG": ".jpg",
            "TIFF": ".tif",
            "BMP": ".bmp",
        }
        intermediate_path = output.with_suffix(ext_by_format[chosen])
        print(f"[render setup] Render intermedio: {intermediate_path}", flush=True)

    try:
        blender_scene.render.film_transparent = True
    except AttributeError:
        pass

    # Samples: override opcional, default respeta el .blend
    if samples is not None:
        if normalized_engine == "CYCLES":
            blender_scene.cycles.samples = samples
        elif normalized_engine == "BLENDER_EEVEE":
            try:
                blender_scene.eevee.taa_render_samples = samples
            except AttributeError:
                pass
        print(f"[render setup] samples override={samples}", flush=True)
    else:
        if normalized_engine == "CYCLES":
            try:
                print(f"[render setup] samples de la escena={blender_scene.cycles.samples}", flush=True)
            except AttributeError:
                pass
        elif normalized_engine == "BLENDER_EEVEE":
            try:
                print(f"[render setup] samples de la escena={blender_scene.eevee.taa_render_samples}", flush=True)
            except AttributeError:
                pass

    # 4. Render
    if needs_conversion:
        # Estrategia robusta: NO escribir a disco con write_still (eso fuerza el
        # file_format de la scene, que está locked a EXR_MULTILAYER). En su
        # lugar: render in-memory, luego usar `Render Result.save_render` con
        # una scene blank cuyos image_settings sí permitan PNG.
        # save_render(scene=fresh_scene) usa los image_settings de fresh_scene
        # como override — bypass total del lock de la scene principal.
        print(
            "[render] Estrategia override: render in-memory → save_render(scene=fresh_scene PNG)",
            flush=True,
        )
        start = _time.time()
        bpy.ops.render.render(write_still=False)
        duration = _time.time() - start

        render_result = bpy.data.images.get("Render Result")
        if render_result is None:
            raise RuntimeError("No existe Render Result tras bpy.ops.render.render")

        fresh_scene = bpy.data.scenes.new("kernel_save_scene")
        try:
            fs_settings = fresh_scene.render.image_settings
            fs_settings.file_format = "PNG"
            try:
                fs_settings.color_mode = "RGBA"
            except TypeError:
                fs_settings.color_mode = "RGB"
            try:
                fs_settings.color_depth = "8"
            except TypeError:
                pass
            # Color management: copiar del original así el PNG guardado
            # respeta view_transform/look/exposure.
            for attr in ("view_transform", "look", "exposure", "gamma", "use_curve_mapping"):
                try:
                    setattr(
                        fresh_scene.view_settings,
                        attr,
                        getattr(blender_scene.view_settings, attr),
                    )
                except (AttributeError, TypeError):
                    pass
            try:
                fresh_scene.display_settings.display_device = (
                    blender_scene.display_settings.display_device
                )
            except (AttributeError, TypeError):
                pass
            render_result.save_render(filepath=str(output), scene=fresh_scene)
        finally:
            try:
                bpy.data.scenes.remove(fresh_scene)
            except Exception:
                pass

        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError(f"save_render terminó pero PNG inválido: {output}")
        print(
            f"[render] PNG escrito: {output} ({output.stat().st_size // 1024} KB)",
            flush=True,
        )
    else:
        # Camino feliz: PNG directo
        blender_scene.render.filepath = str(output)
        start = _time.time()
        bpy.ops.render.render(write_still=True)
        duration = _time.time() - start

        if not output.exists():
            raise RuntimeError(f"Render terminó pero no se encontró: {output}")

    return {
        "output_path": str(output),
        "camera": blender_scene.camera.name,
        "engine": engine,
        "samples": samples,
        "resolution": (resolution_x, resolution_y),
        "duration_seconds": round(duration, 2),
        "file_size_bytes": output.stat().st_size,
        "success": True,
    }


# Placeholder para Fase 3 — las 7 vistas estándar
SEVEN_VIEWS = (
    "Front",
    "Back",
    "Lateral_L",
    "Lateral_R",
    "Iso_L",
    "Iso_R",
    "Hero_Sudada",
)


def run_render_seven_views(*, scene: str, output_dir: str, **kwargs: Any) -> dict[str, Any]:
    """Stub para Fase 3 — itera sobre las 7 cámaras estándar."""
    raise NotImplementedError(
        "render_seven_views pendiente Fase 3. Para Fase 1.5 PoC, usar render_one_view."
    )


def run_render_all_cameras(
    *,
    scene: str,
    output_dir: str,
    output_basename: str | None = None,
    engine: str | None = None,
    samples: int | None = None,
    resolution_x: int | None = None,
    resolution_y: int | None = None,
    output_format: str = "PNG",
) -> dict[str, Any]:
    """Renderiza TODAS las cámaras visibles de la escena en orden.

    Útil cuando el .blend trae varias cámaras estándar (Front, Back, Iso, etc.)
    y queremos un render por cada una sin enumerarlas. Cada PNG sale con
    nombre: {output_basename or 'render'}-{camera_name}.png

    Args:
        scene: Ruta al .blend (ignorado, ya cargada).
        output_dir: Carpeta donde escribir los PNGs.
        output_basename: Prefijo opcional (default: 'render').
        engine, samples, resolution_x, resolution_y: overrides opcionales,
            mismos defaults que run_render_one_view (respetan config del .blend).

    Returns:
        dict con cameras, outputs (lista de paths), success.
    """
    import bpy  # type: ignore[import-not-found]

    cameras = [o for o in bpy.context.scene.objects if o.type == "CAMERA"]
    if not cameras:
        raise ValueError("La escena no tiene cámaras")

    prefix = output_basename or "render"
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Extensión según formato
    fmt_norm = _FORMAT_ALIASES.get(output_format.upper(), "PNG")
    ext = _FORMAT_EXT[fmt_norm]

    results = []
    for cam in cameras:
        # Sanitizar el nombre de cámara para filesystem
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in cam.name)
        output_path = str(out_dir / f"{prefix}-{safe_name}{ext}")
        print(f"[render_all] Cámara '{cam.name}' → {output_path}", flush=True)
        res = run_render_one_view(
            scene=scene,
            output_path=output_path,
            camera_name=cam.name,
            engine=engine,
            samples=samples,
            resolution_x=resolution_x,
            resolution_y=resolution_y,
            output_format=output_format,
        )
        results.append(
            {"camera": cam.name, "output_path": output_path, "duration_seconds": res.get("duration_seconds")}
        )

    return {
        "cameras_rendered": [r["camera"] for r in results],
        "outputs": [r["output_path"] for r in results],
        "output_path": results[0]["output_path"] if results else None,  # primero como "principal"
        "details": results,
        "success": True,
    }


run_render_all = run_render_all_cameras


def run_render_rotations(
    *,
    scene: str,
    output_dir: str,
    output_basename: str | None = None,
    pivot_object_name: str | None = None,
    num_rotations: int = 4,
    engine: str | None = None,
    samples: int | None = None,
    resolution_x: int | None = None,
    resolution_y: int | None = None,
    output_format: str = "PNG",
) -> dict[str, Any]:
    """Renderiza la botella desde N ángulos rotando la CÁMARA alrededor del pivote.

    Workaround para escenas con UNA sola cámara: la cámara orbita alrededor
    del pivote (el centro del objeto principal) y renderizamos en cada ángulo.
    La botella, sus materiales y las luces se mantienen fijas — solo la
    cámara cambia de posición. La iluminación va a verse diferente desde
    cada lado (porque el HDRI/luces tienen direccionalidad), lo cual es
    realista.

    Pivote: usa el `pivot_object_name` si se pasa, o autodetect:
        1) Primer EMPTY de la scene (rigging típico)
        2) MESH con mayor bounding-box volume
    El punto de referencia es la location del pivote (centro mundial).

    Args:
        scene: Ruta al .blend (ignorado, escena ya cargada).
        output_dir: Carpeta donde escribir los PNGs.
        output_basename: Prefijo opcional (default 'render-rot').
        pivot_object_name: Override del pivote. Si None, autodetect.
        num_rotations: Cuántas posiciones (default 4 = 0°, 90°, 180°, 270°).
        engine, samples, resolution_x, resolution_y: overrides opcionales.

    Returns:
        dict con outputs, rotations_deg, pivot_object, success.
    """
    import math
    from mathutils import Matrix, Vector  # type: ignore[import-not-found]

    import bpy  # type: ignore[import-not-found]

    # 1. Encontrar pivote (centro de rotación)
    pivot = None
    if pivot_object_name:
        pivot = bpy.data.objects.get(pivot_object_name)
        if pivot is None:
            raise ValueError(
                f"Objeto pivote '{pivot_object_name}' no existe. "
                f"Disponibles: {[o.name for o in bpy.context.scene.objects]}"
            )
    else:
        empties = [o for o in bpy.context.scene.objects if o.type == "EMPTY"]
        if empties:
            pivot = empties[0]
            print(f"[render_rot] pivote auto: EMPTY '{pivot.name}'", flush=True)
        else:
            meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
            if not meshes:
                raise ValueError("La escena no tiene MESH ni EMPTY para usar como pivote")
            def _bbox_vol(o):
                b = o.bound_box
                xs = [v[0] for v in b]
                ys = [v[1] for v in b]
                zs = [v[2] for v in b]
                return (max(xs) - min(xs)) * (max(ys) - min(ys)) * (max(zs) - min(zs))
            pivot = max(meshes, key=_bbox_vol)
            print(f"[render_rot] pivote auto: MESH '{pivot.name}' (mayor bbox)", flush=True)

    # 2. Camera setup
    cam = bpy.context.scene.camera
    if cam is None:
        cam = next((o for o in bpy.context.scene.objects if o.type == "CAMERA"), None)
        if cam is None:
            raise ValueError("La escena no tiene cámara")
        bpy.context.scene.camera = cam

    pivot_center = pivot.matrix_world.translation.copy()
    cam_pos_original = cam.location.copy()
    cam_rot_original = cam.rotation_euler.copy()
    # Offset de cámara respecto al pivote (en world space, solo XY importa para
    # el orbit; Z se preserva)
    cam_offset = cam_pos_original - pivot_center

    # CRÍTICO: si la cámara tiene animation_data con keyframes, los keyframes
    # sobrescriben los cambios manuales de location/rotation en cada eval del
    # depsgraph. Desactivamos temporalmente la animación y la restauramos al final.
    saved_action = None
    if cam.animation_data and cam.animation_data.action:
        saved_action = cam.animation_data.action
        cam.animation_data.action = None
        print(
            f"[render_rot] animation_data.action desactivado temporalmente ({saved_action.name})",
            flush=True,
        )

    prefix = output_basename or "render-rot"
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fmt_norm = _FORMAT_ALIASES.get(output_format.upper(), "PNG")
    ext = _FORMAT_EXT[fmt_norm]

    results = []
    try:
        for i in range(num_rotations):
            angle_rad = i * (2 * math.pi / num_rotations)
            angle_deg = int(round(math.degrees(angle_rad)))

            # Rotar tanto el offset COMO la rotación de la cámara por θ alrededor
            # del eje Z mundial. Esto preserva el "tilt" original (inclinación
            # arriba/abajo de la cámara) y solo cambia el yaw — como agarrar
            # la cámara y orbitarla manteniendo el ángulo de inclinación fijo.
            rot_z = Matrix.Rotation(angle_rad, 4, "Z")
            new_offset = rot_z @ cam_offset
            cam.location = pivot_center + new_offset

            # Componer rotación original con rotación Z
            original_mat = cam_rot_original.to_matrix().to_4x4()
            new_mat = rot_z @ original_mat
            cam.rotation_euler = new_mat.to_euler()

            # Forzar update del depsgraph para que matrix_world refleje los cambios
            bpy.context.view_layer.update()

            output_path = str(out_dir / f"{prefix}-{angle_deg:03d}deg{ext}")
            print(
                f"[render_rot] Rotación {i+1}/{num_rotations}: cámara a {angle_deg}° "
                f"loc={tuple(round(v,2) for v in cam.location)} "
                f"mw_loc={tuple(round(v,2) for v in cam.matrix_world.translation)} "
                f"→ {output_path}",
                flush=True,
            )
            res = run_render_one_view(
                scene=scene,
                output_path=output_path,
                engine=engine,
                samples=samples,
                resolution_x=resolution_x,
                resolution_y=resolution_y,
                output_format=output_format,
            )
            results.append(
                {
                    "angle_deg": angle_deg,
                    "output_path": output_path,
                    "duration_seconds": res.get("duration_seconds"),
                }
            )
    finally:
        # Restaurar animación y posición original de la cámara (no destructivo)
        if saved_action is not None:
            cam.animation_data.action = saved_action
        cam.location = cam_pos_original
        cam.rotation_euler = cam_rot_original

    return {
        "pivot_object": pivot.name,
        "rotations_deg": [r["angle_deg"] for r in results],
        "outputs": [r["output_path"] for r in results],
        "output_path": results[0]["output_path"] if results else None,
        "details": results,
        "success": True,
    }


render_rotations = run_render_rotations


TOOL_SCHEMA_ONE = {
    "name": "render_one_view",
    "description": (
        "Renderiza UNA cámara específica. "
        "Default output_format=PNG (preview en browser, delivery cliente). "
        "Usar output_format=EXR cuando el usuario pida output para post-producción "
        "(Nuke/Fusion/After Effects), color grading HDR, archival, o lo nombre explícitamente."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scene": {"type": "string"},
            "output_path": {"type": "string"},
            "camera_name": {"type": "string"},
            "engine": {
                "type": "string",
                "enum": ["CYCLES", "BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"],
            },
            "samples": {"type": "integer"},
            "resolution_x": {"type": "integer"},
            "resolution_y": {"type": "integer"},
            "output_format": {
                "type": "string",
                "enum": ["PNG", "EXR", "EXR_MULTILAYER"],
                "description": "PNG default. EXR para post-prod (single layer 32-bit HDR). EXR_MULTILAYER guarda passes (diffuse, specular, depth, AOVs) — útil para compositing avanzado.",
            },
        },
        "required": ["scene", "output_path"],
    },
}

render_one_view = run_render_one_view
render_seven_views = run_render_seven_views
