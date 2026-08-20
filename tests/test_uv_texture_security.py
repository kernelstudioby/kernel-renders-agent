import socket

import pytest

from kernel_agent.uv_executor import _validate_remote_texture_url


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.example.com/label.png",
        "https://user:secret@cdn.example.com/label.png",
        "https://127.0.0.1/label.png",
        "https://[::1]/label.png",
        "https://169.254.169.254/latest/meta-data",
        "https://cdn.example.com:8443/label.png",
    ],
)
def test_remote_texture_url_rejects_unsafe_targets(url: str) -> None:
    with pytest.raises(ValueError):
        _validate_remote_texture_url(url)


def test_remote_texture_url_accepts_public_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
        ],
    )
    _validate_remote_texture_url("https://cdn.example.com/label.png")
