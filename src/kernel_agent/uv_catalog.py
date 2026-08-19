"""Escaneo seguro del catálogo local de productos UV pre-renderizados."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

PASS_EXTENSIONS = {".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
TRACK_MATTE_RE = re.compile(r"-track_matte-([A-Za-z0-9_]+)\.", re.IGNORECASE)
LIQUID_RE = re.compile(r"(?:base_label0-)?liquid[-_]([A-Za-z0-9_]+)\.", re.IGNORECASE)


def resolve_products_root(configured_path: str | Path) -> Path:
    root = Path(configured_path).expanduser().resolve()
    nested = root / "Productos"
    return nested if nested.is_dir() else root


def _find_pass(files: list[Path], suffixes: tuple[str, ...]) -> bool:
    return any(
        file.suffix.lower() in PASS_EXTENSIONS
        and any(file.stem.lower().endswith(suffix.lower()) for suffix in suffixes)
        for file in files
    )


def _friendly_name(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip().title()


def _view_metadata(view_dir: Path) -> dict[str, Any] | None:
    files = [path for path in view_dir.iterdir() if path.is_file()]
    has_uv = _find_pass(files, ("uvpass",))
    has_base = _find_pass(files, ("all_white", "base_label1", "base_label0"))
    has_base0 = _find_pass(files, ("base_label0",))
    liquid_names = sorted(
        {
            match.group(1)
            for file in files
            if (match := LIQUID_RE.search(file.name)) is not None
        }
    )
    if not has_uv or (not has_base and not liquid_names):
        return None

    track_regions = sorted(
        {
            match.group(1).replace("_", " ").title()
            for file in files
            if (match := TRACK_MATTE_RE.search(file.name)) is not None
        }
    )
    sudado = view_dir / "sudado"
    has_sudado = sudado.is_dir() and any(path.is_file() for path in sudado.iterdir())
    mode = "dual" if has_base0 or liquid_names else "single"
    if mode == "dual":
        regions = ["Etiqueta", *track_regions]
    elif track_regions:
        regions = [*track_regions, "Resto"]
    else:
        regions = ["Objeto"]
    return {
        "view_id": view_dir.name,
        "name": _friendly_name(view_dir.name),
        "capabilities": {
            "regions": regions,
            "mode": mode,
            "liquid_variants": liquid_names,
            "sudado": has_sudado,
        },
    }


def scan_uv_products(configured_path: str | Path) -> list[dict[str, Any]]:
    root = resolve_products_root(configured_path)
    if not root.is_dir():
        return []

    products: list[dict[str, Any]] = []
    product_dirs = (path for path in root.iterdir() if path.is_dir())
    for product_dir in sorted(product_dirs, key=lambda path: path.name):
        views = [
            metadata
            for view_dir in sorted(
                (path for path in product_dir.iterdir() if path.is_dir()), key=lambda p: p.name
            )
            if (metadata := _view_metadata(view_dir)) is not None
        ]
        if not views:
            continue

        digest = hashlib.sha256()
        total_bytes = 0
        for file in sorted(path for path in product_dir.rglob("*") if path.is_file()):
            stat = file.stat()
            relative = file.relative_to(product_dir).as_posix()
            digest.update(f"{relative}:{stat.st_size}:{stat.st_mtime_ns}".encode())
            total_bytes += stat.st_size

        products.append(
            {
                "product_id": product_dir.name,
                "name": _friendly_name(product_dir.name),
                "revision": digest.hexdigest()[:16],
                "total_bytes": total_bytes,
                "views": views,
            }
        )
    return products


def resolve_view_path(configured_path: str | Path, product_id: str, view_id: str) -> Path:
    """Resuelve IDs del plan dentro de la raíz; bloquea traversal y paths absolutos."""
    root = resolve_products_root(configured_path)
    candidate = (root / product_id / view_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("producto/vista fuera del catálogo UV") from exc
    if not candidate.is_dir() or _view_metadata(candidate) is None:
        raise FileNotFoundError(f"vista UV no disponible: {product_id}/{view_id}")
    return candidate
