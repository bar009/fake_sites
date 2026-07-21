import socket

import pytest

from fakeshop.security import UnsafeUrlError, resolve_public_host, validate_public_url


def test_rejects_non_http_and_credentials():
    with pytest.raises(UnsafeUrlError):
        validate_public_url("file:///etc/passwd")
    with pytest.raises(UnsafeUrlError):
        validate_public_url("https://user:pass@example.com")


def test_rejects_private_dns(monkeypatch):
    resolve_public_host.cache_clear()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))
    ])
    with pytest.raises(UnsafeUrlError):
        validate_public_url("https://internal.example")


def test_accepts_public_dns(monkeypatch):
    resolve_public_host.cache_clear()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
    ])
    assert validate_public_url("https://example.com/path") == "https://example.com/path"
