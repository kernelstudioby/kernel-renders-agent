from kernel_agent.uv_executor import _merge_state


def test_merge_state_applies_levels_blends_colors_and_clamps() -> None:
    scene = {
        "mode": "dual",
        "region_names": ["Etiqueta", "Cap"],
        "liquid_names": ["clear"],
    }

    state = _merge_state(
        scene,
        {
            "edges_threshold": 14,
            "levels": {
                "Etiqueta": {
                    "Base": {"gamma": 2.2, "gain": 5},
                    "Especular": {"in_black": -1, "out_white": 0.4},
                },
                "Unknown": {"Base": {"gamma": 4}},
            },
            "blend": {
                "Etiqueta": {"Base": "Screen", "Color": "Overlay"},
                "Cap": {"Especular": "invalid"},
            },
            "region_colors": {
                "Cap": {"rgb": [300, -4, 60], "opacity": 1.4},
            },
        },
    )

    assert state["edges_threshold"] == 10
    assert state["levels"]["Etiqueta"]["Base"]["gamma"] == 2.2
    assert state["levels"]["Etiqueta"]["Base"]["gain"] == 3
    assert state["levels"]["Etiqueta"]["Especular"]["in_black"] == 0
    assert state["levels"]["Etiqueta"]["Especular"]["out_white"] == 0.4
    assert state["blend"]["Etiqueta"]["Base"] == "Screen"
    assert state["blend"]["Etiqueta"]["Color"] == "Overlay"
    assert state["blend"]["Cap"]["Especular"] == "Add (Linear Dodge)"
    assert state["color"]["Cap"] == {"rgb": (255, 0, 60), "opacity": 1.0}
