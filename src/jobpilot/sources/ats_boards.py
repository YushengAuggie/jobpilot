"""Greenhouse / Lever / Ashby public job-board sources.

All three ATS providers expose unauthenticated JSON APIs scoped to a company slug.
The shapes differ but the role is identical — fetch a list of postings for the slug
and convert them into JobPostings. We register one Source per provider so the user
can list slugs separately under `profile.ats_boards.{provider}`.

Endpoints:
- Greenhouse:  https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
- Lever:       https://api.lever.co/v0/postings/{slug}?mode=json
- Ashby:       https://api.ashbyhq.com/posting-api/job-board/{slug}
"""

from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup

from jobpilot.models import JobPosting, Profile
from jobpilot.sources.base import register

logger = logging.getLogger(__name__)

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def _strip_html(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text("\n").strip()


def _fetch_company_jobs(
    client: httpx.Client, url: str, slug: str
) -> list[dict]:
    try:
        r = client.get(url.format(slug=slug))
        r.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("ATS fetch failed for %s: %s", slug, e)
        return []
    return _extract_jobs(r.json(), url)


def _extract_jobs(payload: dict | list, url_pattern: str) -> list[dict]:
    if "greenhouse.io" in url_pattern:
        return list(payload.get("jobs", []))
    if "lever.co" in url_pattern:
        return list(payload) if isinstance(payload, list) else []
    if "ashbyhq.com" in url_pattern:
        return list(payload.get("jobs", []))
    return []


class GreenhouseSource:
    name = "greenhouse"

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def health(self) -> tuple[bool, str]:
        try:
            r = httpx.get(GREENHOUSE_URL.format(slug="anthropic"), timeout=self.timeout)
            r.raise_for_status()
            return True, "ok"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def list_jobs(self, profile: Profile, limit: int = 0) -> list[JobPosting]:
        postings: list[JobPosting] = []
        with httpx.Client(timeout=self.timeout) as client:
            for slug in profile.ats_boards.greenhouse:
                for job in _fetch_company_jobs(client, GREENHOUSE_URL, slug):
                    title = job.get("title", "")
                    if not title:
                        continue
                    location = (job.get("location") or {}).get("name") or None
                    postings.append(
                        JobPosting(
                            title=title,
                            company=slug,
                            url=job.get("absolute_url", ""),
                            source="greenhouse",
                            jd_text=_strip_html(job.get("content", "")),
                            location=location,
                        )
                    )
                    if limit and len(postings) >= limit:
                        return postings
        return postings


class LeverSource:
    name = "lever"

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def health(self) -> tuple[bool, str]:
        try:
            r = httpx.get(LEVER_URL.format(slug="netflix"), timeout=self.timeout)
            r.raise_for_status()
            return True, "ok"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def list_jobs(self, profile: Profile, limit: int = 0) -> list[JobPosting]:
        postings: list[JobPosting] = []
        with httpx.Client(timeout=self.timeout) as client:
            for slug in profile.ats_boards.lever:
                for job in _fetch_company_jobs(client, LEVER_URL, slug):
                    title = job.get("text", "")
                    if not title:
                        continue
                    cats = job.get("categories") or {}
                    location = cats.get("location")
                    description = job.get("descriptionPlain") or _strip_html(
                        job.get("description", "")
                    )
                    postings.append(
                        JobPosting(
                            title=title,
                            company=slug,
                            url=job.get("hostedUrl", ""),
                            source="lever",
                            jd_text=description,
                            location=location,
                        )
                    )
                    if limit and len(postings) >= limit:
                        return postings
        return postings


class AshbySource:
    name = "ashby"

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def health(self) -> tuple[bool, str]:
        try:
            r = httpx.get(ASHBY_URL.format(slug="ashby"), timeout=self.timeout)
            r.raise_for_status()
            return True, "ok"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def list_jobs(self, profile: Profile, limit: int = 0) -> list[JobPosting]:
        postings: list[JobPosting] = []
        with httpx.Client(timeout=self.timeout) as client:
            for slug in profile.ats_boards.ashby:
                for job in _fetch_company_jobs(client, ASHBY_URL, slug):
                    title = job.get("title", "")
                    if not title:
                        continue
                    description = _strip_html(
                        job.get("descriptionHtml") or job.get("description", "")
                    )
                    postings.append(
                        JobPosting(
                            title=title,
                            company=slug,
                            url=job.get("jobUrl", ""),
                            source="ashby",
                            jd_text=description,
                            location=job.get("location"),
                        )
                    )
                    if limit and len(postings) >= limit:
                        return postings
        return postings


register(GreenhouseSource())
register(LeverSource())
register(AshbySource())
