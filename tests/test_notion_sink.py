"""Tests for the Notion sink."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jobpilot.models import JobPosting, Score, ScoredPosting, Stage
from jobpilot.notion_sink import NotionSink, _row_properties


def _scored(url: str = "https://example.com/jobs/1", score: float = 7.5) -> ScoredPosting:
    return ScoredPosting(
        posting=JobPosting(
            title="Staff Engineer",
            company="Anthropic",
            url=url,
            source="greenhouse",
            jd_text="x",
            stage=Stage.SERIES_C,
            location="Remote",
            salary_text="$220-280k",
        ),
        score=Score(value=score, reasons=["matches Python", "infra signal"]),
    )


pytestmark = pytest.mark.unit


def test_row_properties_shape() -> None:
    props = _row_properties(_scored())
    assert props["Title"]["title"][0]["text"]["content"] == "Staff Engineer"
    assert props["Company"]["rich_text"][0]["text"]["content"] == "Anthropic"
    assert props["Score"]["number"] == 7.5
    assert "matches Python" in props["Why match"]["rich_text"][0]["text"]["content"]
    assert props["URL"]["url"] == "https://example.com/jobs/1"
    assert props["Status"]["select"]["name"] == "New"
    assert props["Source"]["select"]["name"] == "greenhouse"
    assert props["Stage"]["select"]["name"] == "series-c"
    assert props["Salary"]["rich_text"][0]["text"]["content"] == "$220-280k"


def test_row_properties_omits_salary_when_missing() -> None:
    sp = _scored()
    sp.posting.salary_text = None
    props = _row_properties(sp)
    assert "Salary" not in props


def test_row_properties_truncates_long_title() -> None:
    sp = _scored()
    sp.posting.title = "x" * 500
    props = _row_properties(sp)
    assert len(props["Title"]["title"][0]["text"]["content"]) == 200


def test_get_seen_urls_paginates() -> None:
    sink = NotionSink(token="dummy", database_id="db_x")
    sink.client = MagicMock()
    sink.client.databases.query.side_effect = [
        {
            "results": [
                {"properties": {"URL": {"url": "https://a.com/1"}}},
                {"properties": {"URL": {"url": "https://b.com/2"}}},
            ],
            "has_more": True,
            "next_cursor": "cursor-1",
        },
        {
            "results": [{"properties": {"URL": {"url": "https://c.com/3"}}}],
            "has_more": False,
            "next_cursor": None,
        },
    ]

    urls = sink.get_seen_urls()

    assert urls == {"https://a.com/1", "https://b.com/2", "https://c.com/3"}
    assert sink.client.databases.query.call_count == 2
    second_call = sink.client.databases.query.call_args_list[1]
    assert second_call.kwargs["start_cursor"] == "cursor-1"


def test_upsert_inserts_each_row() -> None:
    sink = NotionSink(token="dummy", database_id="db_x")
    sink.client = MagicMock()
    sink.client.pages.create.return_value = {"id": "page_x"}

    inserted = sink.upsert_postings([_scored("https://a/1"), _scored("https://b/2")])

    assert inserted == 2
    assert sink.client.pages.create.call_count == 2
    parent = sink.client.pages.create.call_args_list[0].kwargs["parent"]
    assert parent == {"database_id": "db_x"}


def test_upsert_continues_after_failure() -> None:
    sink = NotionSink(token="dummy", database_id="db_x")
    sink.client = MagicMock()
    sink.client.pages.create.side_effect = [Exception("boom"), {"id": "ok"}]

    inserted = sink.upsert_postings([_scored("https://a/1"), _scored("https://b/2")])

    assert inserted == 1


def test_get_approved_rows_uses_status_filter() -> None:
    sink = NotionSink(token="dummy", database_id="db_x")
    sink.client = MagicMock()
    sink.client.databases.query.return_value = {"results": [{"id": "row"}]}

    rows = sink.get_approved_rows()

    assert rows == [{"id": "row"}]
    kwargs = sink.client.databases.query.call_args.kwargs
    assert kwargs["filter"]["property"] == "Status"
    assert kwargs["filter"]["select"]["equals"] == "Approved"


def test_get_seen_urls_requires_database_id() -> None:
    sink = NotionSink(token="dummy")
    with pytest.raises(RuntimeError):
        sink.get_seen_urls()
