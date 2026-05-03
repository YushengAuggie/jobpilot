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


@pytest.mark.unit
class TestRedirectChain:
    """Ensure each redirect hop is validated BEFORE httpx fetches it. Letting
    httpx auto-redirect would mean a 302 → http://10.0.0.5/admin gets GET'd
    before any final-URL check fires."""

    def test_redirect_to_private_host_blocked_before_fetch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx
        import respx

        from jobpilot._safe_http import safe_get

        # DNS: example.com is public; internal.example is loopback (1 IP set).
        def fake_getaddrinfo(host: str, *a, **kw):  # noqa: ANN001
            if host == "example.com":
                return [(0, 0, 0, "", ("93.184.216.34", 0))]
            if host == "internal.example":
                return [(0, 0, 0, "", ("127.0.0.1", 0))]
            raise OSError(f"unexpected host {host}")

        monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)

        with respx.mock:
            redirect_route = respx.get("https://example.com/start").mock(
                return_value=httpx.Response(
                    302, headers={"Location": "http://internal.example/admin"}
                )
            )
            internal_route = respx.get("http://internal.example/admin").mock(
                return_value=httpx.Response(200, text="should not be reached")
            )

            with pytest.raises(UnsafeURLError, match="private|loopback"):
                safe_get("https://example.com/start")

            assert redirect_route.called
            assert not internal_route.called  # the whole point — never fetched

    def test_redirect_chain_under_limit_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx
        import respx

        from jobpilot._safe_http import safe_get

        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *a, **kw: [(0, 0, 0, "", ("93.184.216.34", 0))],
        )

        with respx.mock:
            respx.get("https://example.com/a").mock(
                return_value=httpx.Response(302, headers={"Location": "/b"})
            )
            respx.get("https://example.com/b").mock(
                return_value=httpx.Response(200, text="final")
            )

            resp = safe_get("https://example.com/a")
            assert resp.status_code == 200
            assert resp.text == "final"

    def test_redirect_loop_raises_too_many_redirects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx
        import respx

        from jobpilot._safe_http import safe_get

        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *a, **kw: [(0, 0, 0, "", ("93.184.216.34", 0))],
        )

        with respx.mock:
            respx.get("https://example.com/loop").mock(
                return_value=httpx.Response(
                    302, headers={"Location": "https://example.com/loop"}
                )
            )

            with pytest.raises(httpx.TooManyRedirects):
                safe_get("https://example.com/loop", max_redirects=3)
