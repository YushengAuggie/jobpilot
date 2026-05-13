"""Fetch a job description from a URL. Best-effort plain-text extraction.

URLs come from Notion rows whose origin is attacker-influenced (HN comments
plant arbitrary links). We route every fetch through `safe_get` to refuse:
- non-http(s) schemes (file://, javascript:, data:)
- private/loopback hosts (no SSRF to localhost or RFC1918)
- responses bigger than the size cap (no memory DoS)
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from jobpilot._safe_http import ResponseTooLargeError, UnsafeURLError, safe_get


def fetch_jd_text(url: str, timeout: float = 15.0, max_chars: int = 20_000) -> str:
    """GET the URL and return the page's plain text. Caps length to avoid huge JDs.

    Raises:
        UnsafeURLError: scheme/host refused (logged + propagated; caller should
            handle by skipping this row).
        ResponseTooLargeError: body exceeded the safe-fetch cap.
        httpx.HTTPError: transport failure, 4xx/5xx.
    """
    # Note: UnsafeURLError and ResponseTooLargeError both propagate; callers in
    # cli.py wrap them with friendly_errors so the user sees a one-liner.
    _ = (UnsafeURLError, ResponseTooLargeError)  # imported for re-export
    r = safe_get(url, timeout=timeout)
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text("\n").strip()
    return text[:max_chars]
