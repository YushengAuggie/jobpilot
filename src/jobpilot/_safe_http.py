"""HTTP fetch helpers that refuse to walk into SSRF / DoS traps.

Every source and the JD fetcher use this instead of httpx.get() directly. The
cost of forgetting is real: a malicious HN comment can plant a link that, when
followed by `jobpilot tailor` running on a developer's laptop, would otherwise
let httpx happily redirect to localhost or stream a many-gigabyte body into
memory.

Three guards:

1. Scheme allowlist — http(s) only. No file://, javascript:, data:, etc.
2. Private/loopback IP blocklist — refuses RFC1918 addresses, link-local,
   loopback. Resolves the hostname first to check.
3. Response-size cap — streams the body and bails when the cap is hit, so a
   trickling attacker can't exhaust memory.

Redirects are still followed (most ATS boards redirect www → bare host), but
each hop is re-validated against the same guards.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB — more than any sane JD or job board page


class UnsafeURLError(Exception):
    """Raised when a URL fails the scheme/host validation up front."""


class ResponseTooLargeError(Exception):
    """Raised when streamed response exceeds the size cap mid-fetch."""


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
    )


def _validate_url(url: str) -> str:
    """Reject URLs we won't fetch. Returns the url unchanged on success."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"refusing scheme {parsed.scheme!r} (only http/https allowed)")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError(f"no hostname in URL: {url!r}")

    # If host is an IP literal, check it directly. Otherwise resolve and check
    # all returned addresses — DNS rebinding is theoretical here but cheap to
    # block.
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (OSError, socket.gaierror):
        # Resolution failure is the caller's problem; let httpx report it.
        return url
    private = [a for a in addrs if _is_private_ip(a)]
    if private:
        raise UnsafeURLError(f"refusing private/loopback host {host} ({private})")
    return url


def safe_get(
    url: str,
    *,
    timeout: float = 15.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
    follow_redirects: bool = True,
    headers: dict[str, str] | None = None,
    max_redirects: int = 10,
) -> httpx.Response:
    """GET with scheme/host/size guards. Returns a Response with .text materialized.

    Redirects are followed manually so each hop is validated BEFORE httpx fetches
    it. Letting httpx auto-redirect would mean an attacker page can 302 to
    http://10.0.0.5/admin and httpx GETs the internal URL before any final-URL
    validation runs.
    """
    _validate_url(url)
    client_kwargs: dict = {
        "timeout": timeout,
        "follow_redirects": False,  # manual redirect handling — see docstring
    }
    if headers:
        client_kwargs["headers"] = headers

    current_url = url
    with httpx.Client(**client_kwargs) as client:
        for _ in range(max_redirects + 1):
            with client.stream("GET", current_url) as resp:
                if follow_redirects and 300 <= resp.status_code < 400:
                    location = resp.headers.get("location")
                    if location:
                        next_url = str(httpx.URL(current_url).join(location))
                        _validate_url(next_url)
                        current_url = next_url
                        continue
                resp.raise_for_status()
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise ResponseTooLargeError(
                            f"response from {current_url} exceeded {max_bytes} bytes"
                        )
                # Re-create the Response with the buffered body so .text and
                # .json() work after the stream context exits.
                return httpx.Response(
                    status_code=resp.status_code,
                    headers=resp.headers,
                    content=bytes(body),
                    request=resp.request,
                )
        raise httpx.TooManyRedirects(
            f"exceeded {max_redirects} redirects starting at {url}"
        )
