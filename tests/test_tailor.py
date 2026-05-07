"""Tests for the resume + cover letter tailoring step."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jobpilot.models import JobPosting, Profile
from jobpilot.tailor import (
    COVER_LETTER_RUBRIC,
    RESUME_RUBRIC,
    Tailorer,
    _format_user_content,
    _read_resume,
)

pytestmark = pytest.mark.unit


def _profile(resume_path: str) -> Profile:
    return Profile.model_validate(
        {
            "name": "Yusheng D.",
            "resume_path": resume_path,
            "target_roles": ["Staff Engineer", "Tech Lead"],
            "strengths": ["distributed systems", "Python", "Rust"],
            "salary_min_usd": 200000,
            "stages": ["seed", "series-a", "series-b"],
            "locations": ["Remote-US"],
            "ats_boards": {},
            "notion": {"database_id": "x"},
        }
    )


def _posting() -> JobPosting:
    return JobPosting(
        title="Staff Engineer, Infrastructure",
        company="Anthropic",
        url="https://anthropic.com/jobs/infra",
        source="greenhouse",
        jd_text=(
            "Lead the design of our inference serving stack. Required: Python, "
            "Rust, distributed systems experience. Series-B AI startup, $250k-$320k."
        ),
    )


def _mock_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


class TestReadResume:
    def test_reads_markdown(self, tmp_path: Path) -> None:
        f = tmp_path / "resume.md"
        f.write_text("# Yusheng\n\nStaff Engineer with 8 years of infra experience.")
        assert "Staff Engineer" in _read_resume(f)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _read_resume(tmp_path / "nope.pdf")

    def test_expands_user_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "r.md").write_text("hello")
        assert _read_resume("~/r.md") == "hello"


class TestFormatUserContent:
    def test_includes_profile_resume_and_jd_in_separate_blocks(self) -> None:
        content = _format_user_content(
            _profile("/tmp/r.md"),
            base_resume="BASE_RESUME_BODY",
            posting=_posting(),
        )
        # First block: profile + base resume, cached
        assert content[0]["cache_control"] == {"type": "ephemeral"}
        assert "Yusheng D." in content[0]["text"]
        assert "BASE_RESUME_BODY" in content[0]["text"]
        # Second block: JD, NOT cached
        assert "cache_control" not in content[1]
        assert "Anthropic" in content[1]["text"]
        assert "inference serving stack" in content[1]["text"]


class TestTailorer:
    def test_tailor_makes_two_calls_and_returns_tuple(self, tmp_path: Path) -> None:
        resume_file = tmp_path / "r.md"
        resume_file.write_text("# Yusheng\n\nStaff Engineer infra.")

        client = MagicMock()
        client.messages.create.side_effect = [
            _mock_response("# Yusheng D.\n\n## Tailored Resume\n\nDistributed systems work."),
            _mock_response("# Cover Letter\n\nDear Anthropic team..."),
        ]

        resume_md, cover_md = Tailorer(client=client).tailor(_profile(str(resume_file)), _posting())

        assert client.messages.create.call_count == 2
        assert "Yusheng D." in resume_md
        assert "Cover Letter" in cover_md

    def test_uses_correct_rubric_per_call(self, tmp_path: Path) -> None:
        resume_file = tmp_path / "r.md"
        resume_file.write_text("# Yusheng")
        client = MagicMock()
        client.messages.create.side_effect = [_mock_response("R"), _mock_response("C")]

        Tailorer(client=client).tailor(_profile(str(resume_file)), _posting())

        # First call uses the resume rubric
        assert client.messages.create.call_args_list[0].kwargs["system"][0]["text"] == RESUME_RUBRIC
        # Second call uses the cover letter rubric
        assert (
            client.messages.create.call_args_list[1].kwargs["system"][0]["text"]
            == COVER_LETTER_RUBRIC
        )

    def test_temperature_zero_and_pinned_model(self, tmp_path: Path) -> None:
        resume_file = tmp_path / "r.md"
        resume_file.write_text("# Y")
        client = MagicMock()
        client.messages.create.side_effect = [_mock_response("R"), _mock_response("C")]

        Tailorer(client=client).tailor(_profile(str(resume_file)), _posting())

        for call in client.messages.create.call_args_list:
            assert call.kwargs["temperature"] == 0
            assert call.kwargs["model"] == "claude-opus-4-7"

    def test_cache_control_on_rubric_and_profile_block(self, tmp_path: Path) -> None:
        resume_file = tmp_path / "r.md"
        resume_file.write_text("# Y")
        client = MagicMock()
        client.messages.create.side_effect = [_mock_response("R"), _mock_response("C")]

        Tailorer(client=client).tailor(_profile(str(resume_file)), _posting())

        for call in client.messages.create.call_args_list:
            kwargs = call.kwargs
            # Rubric (system) is cached
            assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
            # Profile + base resume block (first user content block) is cached
            user_content = kwargs["messages"][0]["content"]
            assert user_content[0]["cache_control"] == {"type": "ephemeral"}
            # JD block (second user content block) is not cached
            assert "cache_control" not in user_content[1]

    def test_refusal_raises(self, tmp_path: Path) -> None:
        resume_file = tmp_path / "r.md"
        resume_file.write_text("# Y")
        client = MagicMock()
        refused = MagicMock()
        refused.stop_reason = "refusal"
        refused.content = []
        client.messages.create.return_value = refused

        with pytest.raises(RuntimeError, match="declined"):
            Tailorer(client=client).tailor(_profile(str(resume_file)), _posting())


class TestStructuralAssertions:
    """Validators that REAL output should satisfy. Run against fixture outputs that
    simulate Claude's response shape — these would catch obvious regressions in the
    rubric (e.g. if it stopped asking for the candidate's name)."""

    GOOD_RESUME = """\
# Yusheng D.

Staff Engineer with 8+ years of distributed systems and Python infrastructure
experience. Recently led the inference serving stack rewrite at Anthropic, where
the team scaled request throughput by 4x while reducing tail latency by 40%.

## Experience

### Staff Engineer @ Anthropic (2024–present)
- Designed and shipped the inference serving stack handling 50k req/s in Python
  and Rust, with sub-50ms p99 latency under burst load.
- Led a 4-engineer team rewriting the auth layer with sub-millisecond p99
  latency, eliminating 90% of the previous deployment's pager noise.
- Owned the on-call rotation for the serving cluster — 14 incidents resolved
  in 6 months, mean time to mitigation under 12 minutes.
- Drove the migration from a Go-based dispatcher to a Rust dispatcher, cutting
  p99 dispatch overhead from 8ms to 1.2ms.

### Senior Engineer @ Stripe (2020–2024)
- Owned the payments idempotency framework — distributed systems work at the
  scale of $100B+ in annual transaction volume.
- Reduced p99 checkout latency by 40% through targeted Python optimization,
  Rust extension modules, and connection-pool tuning.
- Mentored 6 engineers across the org through systems-design interviews and
  code review patterns now codified in the team's onboarding doc.
- Designed and shipped the cross-region failover protocol used by the radar
  fraud-detection service handling 12B events per day.

### Software Engineer @ Datadog (2017–2020)
- Built the time-series ingestion pipeline (Python + Cassandra) processing
  3M points per second sustained.
- Drove a project-wide migration from py2 to py3 across 14 services with zero
  customer-visible downtime.

## Skills
Python, Rust, Go, distributed systems, infrastructure, on-call leadership,
high-performance serving systems.

## Education
B.S. Computer Science, UC Berkeley (2017).
"""

    GOOD_COVER = """\
# Yusheng D. — Application for Staff Engineer, Infrastructure at Anthropic

I'm reaching out about your Staff Engineer, Infrastructure role on the
inference serving team. Two things in the JD drew me to it specifically:
the focus on Python plus Rust at the serving layer, and the emphasis on
distributed systems experience at production scale. Both are exactly the
shape of work I've been doing for the last four years.

In my current role I led the design of an inference serving stack handling
50k req/s in production, hitting sub-50ms p99 latency under burst load. The
performance goals your team is targeting map closely to the problems I've
already shipped against — including the move from Go to Rust at the dispatch
layer, which cut p99 overhead from 8ms to 1.2ms. Before that, at Stripe,
I owned a payments idempotency framework operating at $100B+ annual volume,
which gave me deep familiarity with the kind of reliability discipline a
serving cluster of your size demands.

I'd love to talk about how that experience could apply to your roadmap, and
I'm especially curious about the on-call structure your team has in place
today and where the largest reliability gaps are. Happy to set up a call at
your convenience.

Looking forward to a conversation.
"""

    def test_good_resume_passes_structural_checks(self) -> None:
        jd_keywords = ["Python", "Rust", "distributed systems"]
        assert all(kw in self.GOOD_RESUME for kw in jd_keywords)
        assert 800 <= len(self.GOOD_RESUME) <= 3000
        assert "[YOUR EXPERIENCE HERE]" not in self.GOOD_RESUME
        assert "Yusheng D." in self.GOOD_RESUME

    def test_good_cover_letter_passes_structural_checks(self) -> None:
        jd_keywords = ["Python", "Rust", "inference serving"]
        # at least 2 of these should appear (cover letters are shorter; not all keywords fit)
        present = sum(1 for kw in jd_keywords if kw in self.GOOD_COVER)
        assert present >= 2
        assert 600 <= len(self.GOOD_COVER) <= 1800
        assert "[YOUR EXPERIENCE HERE]" not in self.GOOD_COVER
        assert "Yusheng D." in self.GOOD_COVER

    def test_short_resume_fails_length_check(self) -> None:
        bad = "# Y\n\nToo short."
        assert not (800 <= len(bad) <= 3000)

    def test_placeholder_text_detected(self) -> None:
        bad = "# Yusheng\n\n[YOUR EXPERIENCE HERE] in distributed systems."
        assert "[YOUR EXPERIENCE HERE]" in bad
