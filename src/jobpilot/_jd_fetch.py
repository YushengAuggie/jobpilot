"""Fetch a job description from a URL. Best-effort plain-text extraction."""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup


def fetch_jd_text(url: str, timeout: float = 15.0, max_chars: int = 20_000) -> str:
    """GET the URL and return the page's plain text. Caps length to avoid huge JDs.

    Caller should pass the resulting string into the Tailorer; it's already capped
    to a reasonable size for prompt context.
    """
    r = httpx.get(url, follow_redirects=True, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text("\n").strip()
    return text[:max_chars]
