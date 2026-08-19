"""Executor CPU para UV Lab V2; compone pases locales sin abrir Blender."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# OpenCV lee EXR solo si la bandera existe ANTES de importar el módulo.
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import cv2
import httpx
import numpy as np

from . import uv_engine_core as core
from .uv_catalog import resolve_view_path


@dataclass
class UvExecuteResult:
    success: bool
    duration_seconds: float
    steps_total: int
    steps_executed: int
    outputs: list[Path] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    failed_step: dict[str, Any] | None = None


_CACHE: OrderedDict[str, tuple[dict[str, Any], int]] = OrderedDict()
_CACHE_BYTES = 0
_CACHE_LOCK = threading.Lock()
_TEXTURE_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_TEXTURE_CACHE_LOCK = threading.Lock()
_PREVIEW_RENDER_LOCK = threading.Lock()
_MAX_TEXTURE_CACHE_ITEMS = 12
_CLEAN_UV_CACHE: OrderedDict[tuple[int, float], tuple[np.ndarray, np.ndarray]] = OrderedDict()
_CLEAN_UV_CACHE_LOCK = threading.Lock()
_MAX_CLEAN_UV_CACHE_ITEMS = 8


def _nbytes(value: Any) -> int:
    if isinstance(value, np.ndarray):
        return value.nbytes
    if isinstance(value, dict):
        return sum(_nbytes(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_nbytes(item) for item in value)
    return 0


def _downsample_scene(scene: dict[str, Any], max_dim: int) -> dict[str, Any]:
    if max(scene["U"].shape[:2]) <= max_dim:
        return scene
    out = dict(scene)
    out["U"] = core.fit_display_2d_coords(scene["U"], max_dim)
    out["V"] = core.fit_display_2d_coords(scene["V"], max_dim)
    out["label_region_mask"] = core.fit_display_2d(scene["label_region_mask"], max_dim)
    out["edges_mask"] = None
    if scene.get("output_alpha") is not None:
        out["output_alpha"] = core.fit_display_2d(scene["output_alpha"], max_dim)
    if scene.get("sudado"):
        sudado = scene["sudado"]
        out["sudado"] = {
            "normal": core.fit_display(sudado["normal"], max_dim),
            "spec": core.fit_display(sudado["spec"], max_dim)
            if sudado.get("spec") is not None
            else None,
        }

    def weight(value: np.ndarray) -> np.ndarray:
        resized = core.fit_display(value, max_dim)
        return resized[:, :, None] if resized.ndim == 2 else resized

    if scene["mode"] == "dual":
        out["base_label0"] = core.fit_display(scene["base_label0"], max_dim)
        out["base_label1"] = core.fit_display(scene["base_label1"], max_dim)
        out["spec"] = core.fit_display(scene["spec"], max_dim)
        if scene.get("liquid_variants"):
            out["liquid_variants"] = {
                key: core.fit_display(value, max_dim)
                for key, value in scene["liquid_variants"].items()
            }
        if scene.get("region_weights"):
            out["region_weights"] = {
                key: weight(value) for key, value in scene["region_weights"].items()
            }
    else:
        out["base"] = core.fit_display(scene["base"], max_dim)
        out["spec"] = core.fit_display(scene["spec"], max_dim)
        out["background_weight"] = weight(scene["background_weight"])
        out["region_weights"] = {
            key: weight(value) for key, value in scene["region_weights"].items()
        }
    return out


def _cached_scene(view_path: Path, max_dim: int, cache_max_mb: int) -> dict[str, Any]:
    global _CACHE_BYTES
    newest_mtime = max(path.stat().st_mtime_ns for path in view_path.rglob("*") if path.is_file())
    key = f"{view_path}:{newest_mtime}:{max_dim}"
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached:
            _CACHE.move_to_end(key)
            return cached[0]

    scene = _downsample_scene(core.build_scene(str(view_path)), max_dim)
    size = _nbytes(scene)
    limit = max(64, cache_max_mb) * 1024 * 1024
    with _CACHE_LOCK:
        while _CACHE and _CACHE_BYTES + size > limit:
            _, (_, removed_size) = _CACHE.popitem(last=False)
            _CACHE_BYTES -= removed_size
        if size <= limit:
            _CACHE[key] = (scene, size)
            _CACHE_BYTES += size
    return scene


def _materialize_label(source: str, work_dir: Path) -> Path:
    if source.lower().startswith(("http://", "https://")):
        target = work_dir / "label"
        with httpx.stream("GET", source, follow_redirects=True, timeout=60) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            suffix = ".png" if "png" in content_type else ".img"
            target = target.with_suffix(suffix)
            with target.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        return target
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"etiqueta no encontrada: {source}")
    return path


def _cached_texture(source: str) -> dict[str, Any]:
    with _TEXTURE_CACHE_LOCK:
        cached = _TEXTURE_CACHE.get(source)
        if cached is not None:
            _TEXTURE_CACHE.move_to_end(source)
            return cached

    with tempfile.TemporaryDirectory(prefix="kernel_uv_texture_") as temp:
        label_path = _materialize_label(source, Path(temp))
        texture = core._load_export_texture(str(label_path))

    with _TEXTURE_CACHE_LOCK:
        _TEXTURE_CACHE[source] = texture
        _TEXTURE_CACHE.move_to_end(source)
        while len(_TEXTURE_CACHE) > _MAX_TEXTURE_CACHE_ITEMS:
            _TEXTURE_CACHE.popitem(last=False)
    return texture


def _merge_state(scene: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    regions = scene["region_names"]
    offset_target = (
        core.LABEL_REGION_NAME
        if scene["mode"] == "dual"
        else ("Body" if "Body" in regions else "Resto" if "Resto" in regions else regions[0])
    )
    state = core.build_neutral_state(regions, offset_target, scene.get("liquid_names"))
    for key in (
        "offset_x",
        "offset_y",
        "art_opacity",
        "selected_liquid",
        "sudado_enabled",
        "sudado_strength",
    ):
        if key in overrides:
            state[key] = overrides[key]
    state["edges_threshold"] = float(
        max(0, min(10, overrides.get("edges_threshold", state["edges_threshold"])))
    )

    level_limits = {
        "in_black": (0.0, 1.0),
        "in_white": (0.0, 1.0),
        "gamma": (0.1, 5.0),
        "out_black": (0.0, 1.0),
        "out_white": (0.0, 1.0),
        "gain": (0.0, 3.0),
    }
    for region, passes in overrides.get("levels", {}).items():
        if region not in state["levels"] or not isinstance(passes, dict):
            continue
        for pass_name in ("Base", "Especular"):
            values = passes.get(pass_name)
            if not isinstance(values, dict):
                continue
            for key, (minimum, maximum) in level_limits.items():
                if key in values:
                    state["levels"][region][pass_name][key] = float(
                        max(minimum, min(maximum, values[key]))
                    )

    for region, passes in overrides.get("blend", {}).items():
        if region not in state["blend"] or not isinstance(passes, dict):
            continue
        for pass_name in ("Base", "Especular", "Color"):
            value = passes.get(pass_name)
            if value in core.BLEND_MODES:
                state["blend"][region][pass_name] = value
    for region, color in overrides.get("region_colors", {}).items():
        if region in state["color"] and isinstance(color, dict):
            rgb = color.get("rgb", [128, 128, 128])
            state["color"][region] = {
                "rgb": tuple(int(max(0, min(255, channel))) for channel in rgb[:3]),
                "opacity": float(max(0, min(1, color.get("opacity", 0)))),
            }
    return state


def _scene_with_clean_uv(scene: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    threshold = round(float(state.get("edges_threshold", core.DEFAULT_EDGES_THRESHOLD)), 3)
    if threshold <= 0:
        return scene
    key = (id(scene), threshold)
    with _CLEAN_UV_CACHE_LOCK:
        cached = _CLEAN_UV_CACHE.get(key)
        if cached is not None:
            _CLEAN_UV_CACHE.move_to_end(key)
            u_clean, v_clean = cached
        else:
            u_clean, v_clean = core.clean_uv_seams(scene["U"], scene["V"], threshold)
            _CLEAN_UV_CACHE[key] = (u_clean, v_clean)
            _CLEAN_UV_CACHE.move_to_end(key)
            while len(_CLEAN_UV_CACHE) > _MAX_CLEAN_UV_CACHE_ITEMS:
                _CLEAN_UV_CACHE.popitem(last=False)
    out = dict(scene)
    out["U"] = u_clean
    out["V"] = v_clean
    return out


def run_uv_compose(
    *,
    products_dir: str,
    output_dir: str,
    product_id: str,
    view_id: str,
    label_url: str,
    state: dict[str, Any] | None = None,
    max_dim: int = 900,
    cache_max_mb: int = 768,
    output_name: str | None = None,
) -> dict[str, Any]:
    view_path = resolve_view_path(products_dir, product_id, view_id)
    max_dim = max(256, min(2048, int(max_dim)))
    scene = _cached_scene(view_path, max_dim, cache_max_mb)
    render_state = _merge_state(scene, state or {})
    render_scene = _scene_with_clean_uv(scene, render_state)

    with tempfile.TemporaryDirectory(prefix="kernel_uv_") as temp:
        label_path = _materialize_label(label_url, Path(temp))
        texture = core._load_export_texture(str(label_path))
        composite = core.render_frame(
            render_scene,
            texture,
            render_state,
            bool((state or {}).get("wrap", False)),
            core.FLIP_V,
            use_mipmap=bool((state or {}).get("mipmap", False)),
        )

    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(
        char if char.isalnum() or char in "-_" else "_"
        for char in (output_name or f"uv-{product_id}-{view_id}")
    )
    output = out_dir / f"{safe_name}.png"
    srgb = (core.linear_to_srgb(composite) * 255.0).clip(0, 255).astype(np.uint8)
    alpha = scene.get("output_alpha")
    if alpha is not None:
        if alpha.shape[:2] != srgb.shape[:2]:
            alpha = cv2.resize(alpha, (srgb.shape[1], srgb.shape[0]), interpolation=cv2.INTER_AREA)
        srgb = np.dstack([srgb, (np.clip(alpha, 0, 1) * 255).astype(np.uint8)])
    if not cv2.imwrite(str(output), srgb):
        raise OSError(f"no se pudo escribir {output}")
    return {"outputs": [{"path": str(output)}], "mode": scene["mode"]}


def render_uv_preview_png(
    *,
    products_dir: str,
    product_id: str,
    view_id: str,
    label_url: str,
    state: dict[str, Any] | None = None,
    max_dim: int = 640,
    cache_max_mb: int = 768,
) -> bytes:
    """Compone un preview PNG en memoria para la API loopback del agent."""
    view_path = resolve_view_path(products_dir, product_id, view_id)
    max_dim = max(256, min(900, int(max_dim)))
    scene = _cached_scene(view_path, max_dim, cache_max_mb)
    render_state = _merge_state(scene, state or {})
    render_scene = _scene_with_clean_uv(scene, render_state)
    texture = _cached_texture(label_url)

    # OpenCV/Numpy liberan el GIL en parte, pero el motor original mantiene
    # buffers compartidos. Serializar previews evita picos de RAM al arrastrar.
    with _PREVIEW_RENDER_LOCK:
        composite = core.render_frame(
            render_scene,
            texture,
            render_state,
            bool((state or {}).get("wrap", False)),
            core.FLIP_V,
            use_mipmap=bool((state or {}).get("mipmap", False)),
        )

    srgb = (core.linear_to_srgb(composite) * 255.0).clip(0, 255).astype(np.uint8)
    alpha = scene.get("output_alpha")
    if alpha is not None:
        if alpha.shape[:2] != srgb.shape[:2]:
            alpha = cv2.resize(alpha, (srgb.shape[1], srgb.shape[0]), interpolation=cv2.INTER_AREA)
        srgb = np.dstack([srgb, (np.clip(alpha, 0, 1) * 255).astype(np.uint8)])
    ok, encoded = cv2.imencode(".png", srgb)
    if not ok:
        raise OSError("no se pudo codificar el preview UV")
    return encoded.tobytes()


def execute_uv_plan(
    plan: list[dict[str, Any]],
    products_dir: str,
    output_dir: str,
    cache_max_mb: int,
    on_step: Callable[[int, int, str], None] | None = None,
) -> UvExecuteResult:
    started = time.time()
    outputs: list[Path] = []
    logs: list[str] = []
    for index, step in enumerate(plan):
        tool = step.get("tool")
        if tool != "uv_compose":
            return UvExecuteResult(
                False,
                time.time() - started,
                len(plan),
                index,
                outputs,
                "\n".join(logs),
                failed_step={"index": index, "tool": tool, "error": "tool UV desconocido"},
            )
        if on_step:
            on_step(index + 1, len(plan), "Componiendo pases UV sin Blender")
        logs.append(f"[step {index + 1}/{len(plan)}] uv_compose")
        try:
            result = run_uv_compose(
                products_dir=products_dir,
                output_dir=output_dir,
                cache_max_mb=cache_max_mb,
                **step.get("args", {}),
            )
        except Exception as exc:  # noqa: BLE001
            return UvExecuteResult(
                False,
                time.time() - started,
                len(plan),
                index,
                outputs,
                "\n".join(logs),
                failed_step={
                    "index": index,
                    "tool": tool,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        outputs.extend(Path(item["path"]) for item in result.get("outputs", []))
    return UvExecuteResult(
        True,
        time.time() - started,
        len(plan),
        len(plan),
        outputs,
        "\n".join(logs),
    )


def is_uv_plan(plan: list[dict[str, Any]]) -> bool:
    return bool(plan) and all(step.get("tool") == "uv_compose" for step in plan)
