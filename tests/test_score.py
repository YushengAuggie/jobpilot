"""Tests for Claude-based scoring."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jobpilot.models import JobPosting, Profile, Stage
from jobpilot.score import Scorer, _format_posting, _format_profile


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


def _mock_response(text: str, stop_reason: str = "end_turn") -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = stop_reason
    response.content = [block]
    return response


@pytest.mark.unit
class TestScorer:
    def test_score_extracts_value_and_reasons(self) -> None:
        client = MagicMock()
        client.messages.create.return_value = _mock_response(
            '{"score": 8.5, "reasons": ["matches distributed-systems", "Series-C outside target"]}'
        )

        score = Scorer(client=client).score(_profile(), _posting())

        assert score.value == 8.5
        assert len(score.reasons) == 2

    def test_strips_markdown_code_fences(self) -> None:
        """Some Anthropic-compatible proxies (Poe, etc.) return JSON wrapped in
        ```json fences. Score must tolerate that — it's the difference between
        works-on-anthropic-only and works-on-any-proxy."""
        client = MagicMock()
        client.messages.create.return_value = _mock_response(
            '```json\n{"score": 7.0, "reasons": ["fence test"]}\n```'
        )

        score = Scorer(client=client).score(_profile(), _posting())

        assert score.value == 7.0
        assert score.reasons == ["fence test"]

    def test_strips_bare_fence_without_language(self) -> None:
        client = MagicMock()
        client.messages.create.return_value = _mock_response(
            '```\n{"score": 6.0, "reasons": ["bare fence"]}\n```'
        )

        score = Scorer(client=client).score(_profile(), _posting())

        assert score.value == 6.0

    def test_unparseable_output_returns_zero_with_warning(self) -> None:
        client = MagicMock()
        client.messages.create.return_value = _mock_response(
            "I think this is a great match! Score: 9 out of 10."
        )

        score = Scorer(client=client).score(_profile(), _posting())

        assert score.value == 0
        assert "unparseable" in score.reasons[0].lower()

    def test_refusal_returns_zero_with_reason(self) -> None:
        client = MagicMock()
        client.messages.create.return_value = _mock_response("", stop_reason="refusal")

        score = Scorer(client=client).score(_profile(), _posting())

        assert score.value == 0
        assert "decline" in score.reasons[0].lower()

    def test_passes_cache_control_on_rubric_and_profile(self) -> None:
        """Caching is the whole point of this module — verify the breakpoints are wired up."""
        client = MagicMock()
        client.messages.create.return_value = _mock_response(
            '{"score": 5.0, "reasons": ["mid"]}'
        )

        Scorer(client=client).score(_profile(), _posting())

        kwargs = client.messages.create.call_args.kwargs
        # System: cache_control on the rubric text block
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        # First user content block (profile) is cached; second (JD) is not
        user_content = kwargs["messages"][0]["content"]
        assert user_content[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in user_content[1]

    def test_uses_pinned_model(self) -> None:
        client = MagicMock()
        client.messages.create.return_value = _mock_response(
            '{"score": 5.0, "reasons": ["mid"]}'
        )

        Scorer(client=client).score(_profile(), _posting())

        # Pinned to a specific model — drift is a deliberate change, not silent
        assert client.messages.create.call_args.kwargs["model"] == "claude-opus-4-7"

    def test_jd_wrapped_in_untrusted_tags(self) -> None:
        """Prompt-injection guard: the JD must be wrapped so the rubric can
        reference it as data, never as instructions."""
        client = MagicMock()
        client.messages.create.return_value = _mock_response(
            '{"score": 5.0, "reasons": ["mid"]}'
        )

        Scorer(client=client).score(_profile(), _posting())

        user_content = client.messages.create.call_args.kwargs["messages"][0]["content"]
        # Second block is the JD — must be wrapped
        jd_text = user_content[1]["text"]
        assert "<untrusted_jd>" in jd_text
        assert "</untrusted_jd>" in jd_text

    def test_reasons_get_url_stripped_and_length_capped(self) -> None:
        """A malicious JD might coerce Claude into emitting URLs/emails into reasons.
        Score must scrub them before persisting to Notion."""
        client = MagicMock()
        client.messages.create.return_value = _mock_response(
            '{"score": 7.0, "reasons": ['
            '"good fit, click https://evil.example/claim",'
            '"contact alice@evil.example for details",'
            '"' + ("x" * 500) + '"'
            "]}"
        )

        score = Scorer(client=client).score(_profile(), _posting())

        # URLs replaced with [link]
        assert "https://" not in score.reasons[0]
        assert "[link]" in score.reasons[0]
        # Emails too
        assert "alice@evil.example" not in score.reasons[1]
        # Long reasons capped
        assert len(score.reasons[2]) <= 240
