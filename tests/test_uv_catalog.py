from pathlib import Path

import pytest

from kernel_agent.uv_catalog import resolve_view_path, scan_uv_products


def test_scan_uv_products_discovers_capabilities(tmp_path: Path) -> None:
    view = tmp_path / "Productos" / "750ml-demo" / "A-FRONT"
    view.mkdir(parents=True)
    for filename in (
        "PASS-uvpass.exr",
        "PASS-all_white.png",
        "PASS-track_matte-Cap.exr",
        "PASS-liquid-orange.exr",
    ):
        (view / filename).write_bytes(b"fixture")
    sudado = view / "sudado"
    sudado.mkdir()
    (sudado / "PASS-shading_normal.exr").write_bytes(b"fixture")

    products = scan_uv_products(tmp_path)

    assert len(products) == 1
    assert products[0]["product_id"] == "750ml-demo"
    capabilities = products[0]["views"][0]["capabilities"]
    assert capabilities == {
        "regions": ["Etiqueta", "Cap"],
        "mode": "dual",
        "liquid_variants": ["orange"],
        "sudado": True,
    }


def test_resolve_view_path_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises((ValueError, FileNotFoundError)):
        resolve_view_path(tmp_path, "..", "outside")
