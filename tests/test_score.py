"""Tests for Claude-based scoring."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jobpilot.models import JobPosting, Profile, Stage
from jobpilot.score import ScoreOutput, Scorer, _format_posting, _format_profile


def _profile(**overrides: object) -> Profile:
    base = {
        "name": "Tester",
        "resume_path": "/tmp/r.pdf",
        "target_roles": ["Staff Engineer", "Tech Lead"],
        "strengths": ["distributed systems", "Python"],
        "salary_min_usd": 200000,
        "stages": ["seed", "series-a", "series-b"],
        "locations": ["Remote-US", "SF"],
        "dealbreakers": ["crypto"],
        "ats_boards": {},
        "notion": {"database_id": "x"},
    }
    base.update(overrides)
    return Profile.model_validate(base)


def _posting(**overrides: object) -> JobPosting:
    base = {
        "title": "Staff Engineer, Infrastructure",
        "company": "Anthropic",
        "url": "https://anthropic.com/jobs/infra",
        "source": "greenhouse",
        "jd_text": "We're hiring infra engineers with distributed-systems experience. SF/Remote.",
        "location": "Remote-US",
        "stage": Stage.SERIES_C,
    }
    base.update(overrides)
    return JobPosting.model_validate(base)


@pytest.mark.unit
class TestFormatting:
    def test_profile_includes_strengths_and_dealbreakers(self) -> None:
        text = _format_profile(_profile())
        assert "distributed systems" in text
        assert "crypto" in text
        assert "<candidate_profile>" in text and "</candidate_profile>" in text

    def test_profile_renders_dealbreakers_none_when_empty(self) -> None:
        text = _format_profile(_profile(dealbreakers=[]))
        assert "(none)" in text

    def test_posting_caps_long_jd(self) -> None:
        huge = "x" * 100_000
        text = _format_posting(_posting(jd_text=huge))
        # capped at JD_CHAR_CAP (6000) plus the surrounding template
        assert text.count("x") == 6000


@pytest.mark.unit
class TestScorer:
    def test_score_extracts_value_and_reasons(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.stop_reason = "end_turn"
        response.parsed_output = ScoreOutput(
            score=8.5,
            reasons=["matches distributed-systems strength", "Series-C — outside seed-B target"],
        )
        client.messages.parse.return_value = response

        score = Scorer(client=client).score(_profile(), _posting())

        assert score.value == 8.5
        assert len(score.reasons) == 2

    def test_refusal_returns_zero_with_reason(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.stop_reason = "refusal"
        response.parsed_output = None
        client.messages.parse.return_value = response

        score = Scorer(client=client).score(_profile(), _posting())

        assert score.value == 0
        assert "decline" in score.reasons[0].lower()

    def test_passes_cache_control_on_rubric_and_profile(self) -> None:
        """Caching is the whole point of this module — verify the breakpoints are wired up."""
        client = MagicMock()
        response = MagicMock()
        response.stop_reason = "end_turn"
        response.parsed_output = ScoreOutput(score=5, reasons=["mid"])
        client.messages.parse.return_value = response

        Scorer(client=client).score(_profile(), _posting())

        kwargs = client.messages.parse.call_args.kwargs
        # System: cache_control on the rubric text block
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        # First user content block (profile) is cached; second (JD) is not
        user_content = kwargs["messages"][0]["content"]
        assert user_content[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in user_content[1]

    def test_uses_pinned_model(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.stop_reason = "end_turn"
        response.parsed_output = ScoreOutput(score=5, reasons=["mid"])
        client.messages.parse.return_value = response

        Scorer(client=client).score(_profile(), _posting())

        # Pinned to a specific model — drift is a deliberate change, not silent
        assert client.messages.parse.call_args.kwargs["model"] == "claude-sonnet-4-6"
