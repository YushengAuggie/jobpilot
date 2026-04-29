"""Filter scored postings against profile constraints. Pure logic — no I/O."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobpilot.models import Profile, ScoredPosting

SALARY_NUMBER_RE = re.compile(r"\$?\s*([\d,]+)\s*(k|K|,000)?")
TRACKING_PARAM_PREFIXES = ("utm_", "gh_", "lever_", "ashby_")


def canonical_url(url: str) -> str:
    """Strip tracking params + fragments + trailing slashes for stable dedup."""
    parts = urlsplit(url)
    cleaned_query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=False)
            if not any(k.startswith(p) for p in TRACKING_PARAM_PREFIXES)
        )
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, cleaned_query, ""))


def parse_min_salary(text: str) -> int | None:
    """Pull the smallest dollar amount out of free-text salary strings.
    Returns None if no number found. We use the minimum to compare against profile floor.

    Handles ranges like '$220-280k' by treating bare numbers as 'k' when any 'k' suffix
    appears in the same string."""
    has_k_in_text = "k" in text.lower()
    matches = SALARY_NUMBER_RE.findall(text)
    values = []
    for raw, suffix in matches:
        n = int(raw.replace(",", ""))
        if suffix and suffix.lower() == "k" or has_k_in_text and n < 1000:
            n *= 1000
        if n >= 1000:
            values.append(n)
    return min(values) if values else None


def has_dealbreaker(jd_text: str, dealbreakers: list[str]) -> str | None:
    """Returns the first matched dealbreaker keyword, or None."""
    haystack = jd_text.lower()
    for kw in dealbreakers:
        if kw.lower() in haystack:
            return kw
    return None


def passes_filters(sp: ScoredPosting, profile: Profile) -> tuple[bool, str | None]:
    """Returns (passed, reason_if_dropped). reason is None when passed."""
    if sp.score.value < profile.score_threshold:
        return False, f"score {sp.score.value:.1f} < threshold {profile.score_threshold}"

    posting = sp.posting

    deal = has_dealbreaker(posting.jd_text, profile.dealbreakers)
    if deal:
        return False, f"dealbreaker: {deal!r}"

    if posting.salary_text:
        min_salary = parse_min_salary(posting.salary_text)
        if min_salary is not None and min_salary < profile.salary_min_usd:
            return False, f"salary {min_salary:,} < min {profile.salary_min_usd:,}"

    if posting.stage not in profile.stages and posting.stage.value != "unknown":
        return False, f"stage {posting.stage.value!r} not in allowlist"

    return True, None
