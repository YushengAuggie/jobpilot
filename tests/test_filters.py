"""Filter, dedup, and salary-parsing tests."""

from __future__ import annotations

import pytest

from jobpilot.filters import (
    canonical_url,
    has_dealbreaker,
    parse_min_salary,
    passes_filters,
)
from jobpilot.models import JobPosting, Profile, Score, ScoredPosting, Stage

pytestmark = pytest.mark.unit


def _profile(**overrides: object) -> Profile:
    base = {
        "name": "Tester",
        "resume_path": "/tmp/r.pdf",
        "target_roles": ["Staff Engineer"],
        "strengths": ["python"],
        "salary_min_usd": 200000,
        "stages": ["seed", "series-a", "series-b"],
        "locations": ["Remote-US"],
        "dealbreakers": ["crypto", "gambling"],
        "ats_boards": {},
        "notion": {"database_id": "x"},
        "score_threshold": 6,
    }
    base.update(overrides)
    return Profile.model_validate(base)


def _scored(score: float = 8.0, **posting_kwargs: object) -> ScoredPosting:
    base = {
        "title": "Staff Engineer",
        "company": "Anthropic",
        "url": "https://anthropic.com/jobs/1",
        "source": "greenhouse",
        "jd_text": "Distributed systems work",
        "stage": Stage.SERIES_A,
    }
    base.update(posting_kwargs)
    return ScoredPosting(
        posting=JobPosting.model_validate(base),
        score=Score(value=score, reasons=["a"]),
    )


class TestCanonicalUrl:
    def test_strips_tracking_params(self) -> None:
        url = "https://Example.com/jobs/123/?utm_source=foo&gh_src=bar&id=42"
        assert canonical_url(url) == "https://example.com/jobs/123?id=42"

    def test_strips_fragment_and_trailing_slash(self) -> None:
        assert canonical_url("https://x.com/a/#fragment") == "https://x.com/a"

    def test_lowercases_host(self) -> None:
        assert canonical_url("https://API.Example.COM/x") == "https://api.example.com/x"

    def test_empty_path_becomes_root(self) -> None:
        assert canonical_url("https://x.com") == "https://x.com/"

    def test_sorts_query_params(self) -> None:
        a = canonical_url("https://x.com/?b=2&a=1")
        b = canonical_url("https://x.com/?a=1&b=2")
        assert a == b


class TestSalaryParse:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("$220-280k", 220_000),
            ("$220,000 - $280,000", 220_000),
            ("100-150k USD", 100_000),
            ("competitive", None),
            ("up to $300,000", 300_000),
            ("80k-95k", 80_000),
        ],
    )
    def test_parses(self, text: str, expected: int | None) -> None:
        assert parse_min_salary(text) == expected


class TestDealbreaker:
    def test_finds_match(self) -> None:
        assert has_dealbreaker("This is a CRYPTO startup", ["crypto"]) == "crypto"

    def test_returns_none_when_no_match(self) -> None:
        assert has_dealbreaker("Pure SaaS company", ["crypto"]) is None

    def test_empty_dealbreakers(self) -> None:
        assert has_dealbreaker("anything", []) is None


class TestPassesFilters:
    def test_passes_clean_match(self) -> None:
        passed, reason = passes_filters(_scored(score=8), _profile())
        assert passed is True
        assert reason is None

    def test_below_threshold_drops(self) -> None:
        passed, reason = passes_filters(_scored(score=5), _profile())
        assert passed is False
        assert "threshold" in (reason or "")

    def test_dealbreaker_drops(self) -> None:
        sp = _scored(jd_text="We are a crypto company building DeFi.")
        passed, reason = passes_filters(sp, _profile())
        assert passed is False
        assert "dealbreaker" in (reason or "")

    def test_below_salary_drops(self) -> None:
        sp = _scored(salary_text="$100,000 - $130,000")
        passed, reason = passes_filters(sp, _profile())
        assert passed is False
        assert "salary" in (reason or "")

    def test_above_salary_passes(self) -> None:
        sp = _scored(salary_text="$220-280k")
        passed, _ = passes_filters(sp, _profile())
        assert passed is True

    def test_unknown_stage_passes(self) -> None:
        sp = _scored(stage=Stage.UNKNOWN)
        passed, _ = passes_filters(sp, _profile())
        assert passed is True

    def test_disallowed_stage_drops(self) -> None:
        sp = _scored(stage=Stage.PUBLIC)
        passed, reason = passes_filters(sp, _profile())
        assert passed is False
        assert "stage" in (reason or "")

    def test_no_salary_text_passes(self) -> None:
        sp = _scored(salary_text=None)
        passed, _ = passes_filters(sp, _profile())
        assert passed is True
