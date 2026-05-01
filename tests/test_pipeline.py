"""Pipeline orchestration tests — wires sources, scorer, filters, sink."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jobpilot.models import JobPosting, Profile, Score, Stage
from jobpilot.pipeline import run_daily
from jobpilot.sources.base import REGISTRY

pytestmark = pytest.mark.unit


def _profile(**overrides: object) -> Profile:
    base = {
        "name": "Tester",
        "resume_path": "/tmp/r.pdf",
        "target_roles": ["Staff Engineer"],
        "strengths": ["python"],
        "salary_min_usd": 0,
        "stages": ["seed", "series-a", "series-b", "series-c"],
        "locations": ["Remote-US"],
        "dealbreakers": [],
        "ats_boards": {},
        "notion": {"database_id": "db_x"},
        "score_threshold": 6,
        "daily_limit": 25,
    }
    base.update(overrides)
    return Profile.model_validate(base)


def _posting(url: str, **kwargs: object) -> JobPosting:
    base = {
        "title": "Engineer",
        "company": "X",
        "url": url,
        "source": "hn",
        "jd_text": "work",
        "stage": Stage.SERIES_A,
    }
    base.update(kwargs)
    return JobPosting.model_validate(base)


@pytest.fixture
def fake_source():
    """Register a fake source named 'fake' for the duration of one test."""
    fs = MagicMock()
    fs.name = "fake"
    REGISTRY["fake"] = fs
    yield fs
    REGISTRY.pop("fake", None)


def test_drops_postings_already_in_notion(fake_source) -> None:
    fake_source.list_jobs.return_value = [
        _posting("https://a.com/1"),
        _posting("https://b.com/2"),
    ]
    sink = MagicMock()
    sink.get_seen_urls.return_value = {"https://a.com/1"}
    sink.upsert_postings.return_value = 1
    scorer = MagicMock()
    scorer.score.return_value = Score(value=8, reasons=["x"])

    summary = run_daily(_profile(), sources=["fake"], sink=sink, scorer=scorer)

    assert summary.fetched == 2
    assert summary.new_after_dedup == 1
    assert scorer.score.call_count == 1
    sink.upsert_postings.assert_called_once()
    assert len(sink.upsert_postings.call_args.args[0]) == 1


def test_dedup_within_same_run(fake_source) -> None:
    fake_source.list_jobs.return_value = [
        _posting("https://a.com/1"),
        _posting("https://a.com/1/?utm_source=foo"),  # canonicalizes to same URL
    ]
    sink = MagicMock()
    sink.get_seen_urls.return_value = set()
    sink.upsert_postings.return_value = 1
    scorer = MagicMock()
    scorer.score.return_value = Score(value=8, reasons=["x"])

    summary = run_daily(_profile(), sources=["fake"], sink=sink, scorer=scorer)

    assert summary.new_after_dedup == 1
    assert scorer.score.call_count == 1


def test_filters_below_threshold(fake_source) -> None:
    fake_source.list_jobs.return_value = [_posting("https://a/1"), _posting("https://b/2")]
    sink = MagicMock()
    sink.get_seen_urls.return_value = set()
    sink.upsert_postings.return_value = 1
    scorer = MagicMock()
    scorer.score.side_effect = [
        Score(value=8, reasons=["good"]),
        Score(value=4, reasons=["weak"]),
    ]

    summary = run_daily(_profile(), sources=["fake"], sink=sink, scorer=scorer)

    assert summary.scored == 2
    assert summary.passed_filters == 1
    sink.upsert_postings.assert_called_once()


def test_dry_run_skips_notion_writes(fake_source) -> None:
    fake_source.list_jobs.return_value = [_posting("https://a/1")]
    sink = MagicMock()
    scorer = MagicMock()
    scorer.score.return_value = Score(value=9, reasons=["match"])

    summary = run_daily(
        _profile(), dry_run=True, sources=["fake"], sink=sink, scorer=scorer
    )

    assert summary.upserted == 0
    sink.upsert_postings.assert_not_called()
    sink.get_seen_urls.assert_not_called()


def test_broken_source_does_not_abort_run(fake_source) -> None:
    fake_source.list_jobs.side_effect = RuntimeError("API down")
    sink = MagicMock()
    sink.get_seen_urls.return_value = set()
    scorer = MagicMock()

    summary = run_daily(_profile(), sources=["fake"], sink=sink, scorer=scorer)

    assert "fake" in summary.sources_broken
    assert summary.sources_ok == []
    assert summary.fetched == 0


def test_caps_at_daily_limit(fake_source) -> None:
    profile = _profile(daily_limit=2)
    fake_source.list_jobs.return_value = [_posting(f"https://a/{i}") for i in range(5)]
    sink = MagicMock()
    sink.get_seen_urls.return_value = set()
    sink.upsert_postings.return_value = 2
    scorer = MagicMock()
    scorer.score.side_effect = [Score(value=v, reasons=["x"]) for v in [9, 8, 7, 7, 7]]

    summary = run_daily(profile, sources=["fake"], sink=sink, scorer=scorer)

    assert summary.passed_filters == 2  # capped to daily_limit
    upserted_arg = sink.upsert_postings.call_args.args[0]
    # Sorted by score descending — top 2 should be the 9 and 8
    assert [sp.score.value for sp in upserted_arg] == [9.0, 8.0]


def test_skip_scoring_bypasses_scorer_and_filters(fake_source) -> None:
    fake_source.list_jobs.return_value = [
        _posting("https://a/1"),
        _posting("https://b/2"),
    ]
    sink = MagicMock()
    scorer = MagicMock()  # should NOT be called

    summary = run_daily(_profile(), skip_scoring=True, sources=["fake"], sink=sink, scorer=scorer)

    scorer.score.assert_not_called()
    sink.upsert_postings.assert_not_called()  # skip_scoring implies dry_run
    sink.get_seen_urls.assert_not_called()
    assert summary.scored == 2  # all postings stubbed with placeholder score
    assert summary.passed_filters == 2  # filters bypassed when skip_scoring


def test_skip_scoring_does_not_construct_notion_sink(
    fake_source, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of --no-score is verifying sources without paying for Anthropic
    AND without setting up Notion. If the user has no NOTION_TOKEN, it should still work."""
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    fake_source.list_jobs.return_value = [_posting("https://a/1")]

    # Pass sink=None and scorer=MagicMock — pipeline must not try to build a NotionSink
    summary = run_daily(
        _profile(),
        skip_scoring=True,
        sources=["fake"],
        sink=None,
        scorer=MagicMock(),
    )

    assert summary.fetched == 1
    assert summary.scored == 1


def test_dealbreaker_pre_filter_runs_before_scoring(fake_source) -> None:
    """An attacker-planted 'crypto' comment must be filtered BEFORE Claude
    is called — otherwise the user pays for scoring obviously-rejected rows."""
    profile = _profile(dealbreakers=["crypto"])
    fake_source.list_jobs.return_value = [
        _posting("https://a/1", jd_text="Building crypto exchange infrastructure"),
        _posting("https://b/2", jd_text="Distributed systems for fintech"),
    ]
    sink = MagicMock()
    sink.get_seen_urls.return_value = set()
    sink.upsert_postings.return_value = 1
    scorer = MagicMock()
    scorer.score.return_value = Score(value=8, reasons=["good"])

    summary = run_daily(profile, sources=["fake"], sink=sink, scorer=scorer)

    # Only the non-crypto posting should reach the scorer
    assert scorer.score.call_count == 1
    assert summary.scored == 1


def test_dry_run_without_skip_scoring_still_avoids_sink_when_provided(fake_source) -> None:
    """dry_run alone (with scoring) shouldn't write to Notion either, but get_seen_urls
    is still skipped — the dedup signal is degraded but the run completes."""
    fake_source.list_jobs.return_value = [_posting("https://a/1")]
    sink = MagicMock()
    scorer = MagicMock()
    scorer.score.return_value = Score(value=8, reasons=["x"])

    summary = run_daily(_profile(), dry_run=True, sources=["fake"], sink=sink, scorer=scorer)

    sink.get_seen_urls.assert_not_called()
    sink.upsert_postings.assert_not_called()
    assert summary.scored == 1


def test_scoring_failure_skips_posting(fake_source) -> None:
    fake_source.list_jobs.return_value = [_posting("https://a/1"), _posting("https://b/2")]
    sink = MagicMock()
    sink.get_seen_urls.return_value = set()
    sink.upsert_postings.return_value = 1
    scorer = MagicMock()
    scorer.score.side_effect = [RuntimeError("scoring blew up"), Score(value=8, reasons=["x"])]

    summary = run_daily(_profile(), sources=["fake"], sink=sink, scorer=scorer)

    assert summary.scored == 1
    assert summary.passed_filters == 1
