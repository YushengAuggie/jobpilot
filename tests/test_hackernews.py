"""Tests for the Hacker News 'Who is Hiring' source."""

from __future__ import annotations

import httpx
import pytest
import respx

from jobpilot.models import Profile
from jobpilot.sources.hackernews import (
    HackerNewsSource,
    _first_url,
    _parse_comment,
    _split_first_line,
)


def _profile() -> Profile:
    return Profile.model_validate(
        {
            "name": "Tester",
            "resume_path": "/tmp/r.pdf",
            "target_roles": ["Senior Engineer"],
            "strengths": ["python"],
            "salary_min_usd": 0,
            "stages": ["seed"],
            "locations": ["Remote-US"],
            "ats_boards": {},
            "notion": {"database_id": "x"},
        }
    )


@pytest.mark.unit
class TestPureLogic:
    def test_split_first_line_pipe(self) -> None:
        assert _split_first_line("Anthropic | Staff Eng | SF") == ("Anthropic", "Staff Eng")

    def test_split_first_line_em_dash(self) -> None:
        assert _split_first_line("Stripe — Senior Engineer") == ("Stripe", "Senior Engineer")

    def test_split_first_line_hyphen(self) -> None:
        assert _split_first_line("Linear - Tech Lead") == ("Linear", "Tech Lead")

    def test_split_first_line_no_separator(self) -> None:
        assert _split_first_line("Plain text with no separator") == (None, None)

    def test_first_url_finds_anchor(self) -> None:
        from bs4 import BeautifulSoup

        html = '<p>See <a href="https://example.com/jobs">here</a></p>'
        soup = BeautifulSoup(html, "html.parser")
        assert _first_url(soup, html) == "https://example.com/jobs"

    def test_first_url_falls_back_to_regex(self) -> None:
        from bs4 import BeautifulSoup

        html = "<p>Apply at https://plain.example.com/role</p>"
        soup = BeautifulSoup(html, "html.parser")
        assert _first_url(soup, html) == "https://plain.example.com/role"

    def test_parse_comment_full(self) -> None:
        html = (
            "<p>Anthropic | Member of Technical Staff | SF / Remote</p>"
            "<p>We&#x27;re hiring for our infrastructure team. "
            "Looking for distributed systems experience.</p>"
            '<p>Apply: <a href="https://anthropic.com/jobs/infra">link</a></p>'
        )
        posting = _parse_comment(99999, html)
        assert posting is not None
        assert posting.company == "Anthropic"
        assert "Member of Technical Staff" in posting.title
        assert posting.url == "https://anthropic.com/jobs/infra"
        assert "distributed systems" in posting.jd_text
        assert posting.source == "hn"

    def test_parse_comment_too_short(self) -> None:
        html = "<p>hi</p>"
        assert _parse_comment(1, html) is None

    def test_parse_comment_no_url_uses_hn_permalink(self) -> None:
        html = "<p>SomeCo | Eng | Remote</p><p>Long enough description text here, lots of words.</p>"
        posting = _parse_comment(42, html)
        assert posting is not None
        assert posting.url == "https://news.ycombinator.com/item?id=42"


@pytest.mark.replay
class TestEndToEnd:
    """Full source pipeline against mocked HTTP. Deterministic."""

    @respx.mock
    def test_list_jobs_smoke(self) -> None:
        algolia_resp = {
            "hits": [{"objectID": "1000000", "title": "Ask HN: Who is hiring? (April 2026)"}]
        }
        story_resp = {"id": 1000000, "kids": [1000001, 1000002, 1000003]}
        comment_a = {
            "id": 1000001,
            "text": (
                "<p>Anthropic | Staff Eng | Remote</p><p>Distributed systems team. "
                'Apply <a href="https://anthropic.com/jobs/x">here</a></p>'
            ),
        }
        comment_b = {
            "id": 1000002,
            "text": "<p>Stripe — Senior Eng — SF</p><p>Payments infrastructure work needed.</p>",
        }
        comment_deleted = {"id": 1000003, "deleted": True}

        respx.get("https://hn.algolia.com/api/v1/search").mock(
            return_value=httpx.Response(200, json=algolia_resp)
        )
        respx.get("https://hacker-news.firebaseio.com/v0/item/1000000.json").mock(
            return_value=httpx.Response(200, json=story_resp)
        )
        respx.get("https://hacker-news.firebaseio.com/v0/item/1000001.json").mock(
            return_value=httpx.Response(200, json=comment_a)
        )
        respx.get("https://hacker-news.firebaseio.com/v0/item/1000002.json").mock(
            return_value=httpx.Response(200, json=comment_b)
        )
        respx.get("https://hacker-news.firebaseio.com/v0/item/1000003.json").mock(
            return_value=httpx.Response(200, json=comment_deleted)
        )

        source = HackerNewsSource(max_comments=10)
        postings = source.list_jobs(_profile())

        assert len(postings) == 2
        companies = {p.company for p in postings}
        assert "Anthropic" in companies
        assert "Stripe" in companies
        assert all(p.source == "hn" for p in postings)
