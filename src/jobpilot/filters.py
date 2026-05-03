"""Filter scored postings against profile constraints. Pure logic — no I/O."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobpilot.models import JobPosting, Profile, ScoredPosting, Stage

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


_K_SUFFIX_RE = re.compile(r"\b\d+\s*k\b", flags=re.IGNORECASE)
# Phrases that look like a salary number with a 'k' suffix but aren't compensation.
_NON_SALARY_K_RE = re.compile(r"\b401\s*\(?k\)?\b", flags=re.IGNORECASE)


def parse_min_salary(text: str) -> int | None:
    """Pull the smallest dollar amount out of free-text salary strings.
    Returns None if no number found. We use the minimum to compare against profile floor.

    Handles ranges like '$220-280k' by treating bare numbers as 'k' when a 'k' suffix
    appears as a standalone token in the same string. Strips known non-salary 'k'
    phrases ('401k', '401(k)') first so a retirement-plan mention can't masquerade
    as a $401,000 base salary."""
    text = _NON_SALARY_K_RE.sub("", text)
    has_k_token = bool(_K_SUFFIX_RE.search(text))
    matches = SALARY_NUMBER_RE.findall(text)
    values = []
    for raw, suffix in matches:
        # SALARY_NUMBER_RE.[\d,]+ can match a lone comma — skip non-numeric.
        cleaned = raw.replace(",", "")
        if not cleaned.isdigit():
            continue
        n = int(cleaned)
        if suffix and suffix.lower() == "k" or has_k_token and n < 1000:
            n *= 1000
        if n >= 1000:
            values.append(n)
    return min(values) if values else None


def dealbreaker_haystack(posting: JobPosting) -> str:
    """Concatenate searchable fields for dealbreaker scanning. Title and company
    matter — a posting titled 'Senior Crypto Engineer' with a clean body should
    still match a 'crypto' dealbreaker."""
    return f"{posting.title} {posting.company} {posting.jd_text}"


def has_dealbreaker(text: str, dealbreakers: list[str]) -> str | None:
    """Returns the first matched dealbreaker keyword, or None.

    Uses word-boundary matching so short keywords ('ai', 'ml', 'ny') don't
    false-match common substrings ('available', 'html', 'any'). Callers that
    want to check title + company + body should concatenate them before
    passing in."""
    if not dealbreakers:
        return None
    haystack = text.lower()
    for kw in dealbreakers:
        pattern = rf"\b{re.escape(kw.lower())}\b"
        if re.search(pattern, haystack):
            return kw
    return None


def passes_filters(sp: ScoredPosting, profile: Profile) -> tuple[bool, str | None]:
    """Returns (passed, reason_if_dropped). reason is None when passed."""
    if sp.score.value < profile.score_threshold:
        return False, f"score {sp.score.value:.1f} < threshold {profile.score_threshold}"

    posting = sp.posting

    deal = has_dealbreaker(dealbreaker_haystack(posting), profile.dealbreakers)
    if deal:
        return False, f"dealbreaker: {deal!r}"

    if posting.salary_text:
        min_salary = parse_min_salary(posting.salary_text)
        if min_salary is not None and min_salary < profile.salary_min_usd:
            return False, f"salary {min_salary:,} < min {profile.salary_min_usd:,}"

    if posting.stage != Stage.UNKNOWN and posting.stage not in profile.stages:
        return False, f"stage {posting.stage.value!r} not in allowlist"

    return True, None
