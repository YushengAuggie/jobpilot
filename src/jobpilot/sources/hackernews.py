"""Hacker News 'Who is Hiring' source.

Strategy:
1. Find the latest 'Ask HN: Who is hiring?' story via Algolia HN search.
2. Fetch its top-level comments via the Firebase HN API.
3. Each comment becomes a JobPosting. Comments are free-form, so we keep the full
   text as the JD and let the scorer extract signal — no fragile parsing here.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import httpx
from bs4 import BeautifulSoup

from jobpilot.models import JobPosting, Profile, Stage
from jobpilot.sources.base import register

ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"

URL_RE = re.compile(r"https?://[^\s<>\"']+")


class HackerNewsSource:
    name = "hn"

    def __init__(self, max_comments: int = 300, timeout: float = 10.0) -> None:
        self.max_comments = max_comments
        self.timeout = timeout

    def list_jobs(self, profile: Profile, limit: int = 0) -> list[JobPosting]:
        with httpx.Client(timeout=self.timeout) as client:
            story_id = self._latest_who_is_hiring_id(client)
            kid_ids = self._story_kids(client, story_id)
            cap = min(self.max_comments, limit) if limit > 0 else self.max_comments
            kid_ids = kid_ids[:cap]
            return self._fetch_comments(client, kid_ids)

    def _latest_who_is_hiring_id(self, client: httpx.Client) -> int:
        r = client.get(
            ALGOLIA_SEARCH,
            params={
                "query": "Ask HN Who is hiring",
                "tags": "story,author_whoishiring",
                "hitsPerPage": "5",
            },
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if not hits:
            raise RuntimeError("No 'Who is hiring' story found via Algolia")
        # Algolia returns most recent first by default for tag-filtered queries
        return int(hits[0]["objectID"])

    def _story_kids(self, client: httpx.Client, story_id: int) -> list[int]:
        r = client.get(HN_ITEM.format(id=story_id))
        r.raise_for_status()
        data = r.json()
        return [int(k) for k in data.get("kids", [])]

    def _fetch_comments(self, client: httpx.Client, ids: list[int]) -> list[JobPosting]:
        def fetch_one(cid: int) -> dict | None:
            try:
                r = client.get(HN_ITEM.format(id=cid))
                r.raise_for_status()
                return r.json()
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(fetch_one, ids))

        postings: list[JobPosting] = []
        for cid, data in zip(ids, results, strict=True):
            if not data or data.get("deleted") or data.get("dead"):
                continue
            html = data.get("text") or ""
            if not html:
                continue
            posting = _parse_comment(cid, html)
            if posting:
                postings.append(posting)
        return postings


def _parse_comment(comment_id: int, html: str) -> JobPosting | None:
    """Convert a single HN comment into a JobPosting.

    HN comments use a small subset of HTML (<p>, <a>, <i>, <pre>). We extract:
    - text: stripped plain text (the JD)
    - first line: heuristic title/company
    - first URL: application link (or HN comment permalink as fallback)
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n").strip()
    if len(text) < 40:
        return None

    first_line = text.split("\n", 1)[0].strip()
    company, title = _split_first_line(first_line)

    url = _first_url(soup, html) or f"https://news.ycombinator.com/item?id={comment_id}"

    return JobPosting(
        title=title or first_line[:80],
        company=company or "(see post)",
        url=url,
        source="hn",
        jd_text=text,
        stage=Stage.UNKNOWN,
    )


def _split_first_line(line: str) -> tuple[str | None, str | None]:
    """HN posts often start with 'Company | Role | Location' or 'Company - Role'.
    Best-effort split; the scorer doesn't depend on this being correct."""
    for sep in (" | ", " — ", " - ", " – "):
        if sep in line:
            parts = [p.strip() for p in line.split(sep)]
            if len(parts) >= 2:
                return parts[0], parts[1]
    return None, None


def _first_url(soup: BeautifulSoup, raw_html: str) -> str | None:
    a = soup.find("a", href=True)
    if a:
        return str(a["href"])
    m = URL_RE.search(raw_html)
    return m.group(0) if m else None


register(HackerNewsSource())
