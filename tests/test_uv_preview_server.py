from kernel_agent.uv_preview_server import allowed_origins


def test_allowed_origins_include_loopback_and_configured_server() -> None:
    origins = allowed_origins("https://kernel.example.com/path")

    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins
    assert "https://kernel.example.com" in origins
    assert "https://kernel.example.com/path" not in origins
