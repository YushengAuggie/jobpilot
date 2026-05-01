"""Unit tests for the SSRF / DoS guards in _safe_http."""

from __future__ import annotations

import socket

import pytest

from jobpilot._safe_http import UnsafeURLError, _validate_url

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "ftp://example.com/file",
    ],
)
def test_rejects_non_http_schemes(url: str) -> None:
    with pytest.raises(UnsafeURLError, match="scheme"):
        _validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "https://127.0.0.1/foo",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
        "http://[::1]/",
    ],
)
def test_rejects_private_and_loopback_hosts(url: str) -> None:
    with pytest.raises(UnsafeURLError, match="private|loopback|host"):
        _validate_url(url)


def test_accepts_normal_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub DNS so we don't actually hit the network.
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(0, 0, 0, "", ("93.184.216.34", 0))],  # example.com
    )
    assert _validate_url("https://example.com/jobs") == "https://example.com/jobs"


def test_resolution_failure_is_left_to_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    """If DNS fails, we don't pre-empt — httpx will raise a clear error."""

    def boom(*a, **kw):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    # Should NOT raise UnsafeURLError; the URL is returned and httpx will fail later.
    assert _validate_url("https://example.com/x") == "https://example.com/x"


def test_no_hostname_rejected() -> None:
    with pytest.raises(UnsafeURLError, match="hostname"):
        _validate_url("https:///path-only")
