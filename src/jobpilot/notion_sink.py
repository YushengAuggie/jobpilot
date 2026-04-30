"""Notion as persistent state: dedup keys, scored postings, lifecycle status.

Schema (created by ensure_database):
  Title       — title
  Company     — rich_text
  Score       — number (0-10)
  Why match   — rich_text (bullet reasons joined by newlines)
  Salary      — rich_text
  Stage       — select
  URL         — url   (dedup key)
  Status      — select: New / Approved / Materials-Ready / Submitted / Rejected / Skip
  Source      — select: yc / greenhouse / lever / ashby / hn / linkedin
  Found       — date
"""

from __future__ import annotations

import logging
from typing import Any

from notion_client import Client

from jobpilot.filters import canonical_url
from jobpilot.models import ScoredPosting, Stage

logger = logging.getLogger(__name__)

DB_TITLE = "jobpilot — Job Shortlist"
TITLE_MAX = 200

STATUS_OPTIONS = [
    {"name": "New", "color": "blue"},
    {"name": "Approved", "color": "green"},
    {"name": "Materials-Ready", "color": "purple"},
    {"name": "Submitted", "color": "yellow"},
    {"name": "Rejected", "color": "red"},
    {"name": "Skip", "color": "gray"},
]

SOURCE_OPTIONS = [
    {"name": s, "color": c}
    for s, c in [
        ("yc", "orange"),
        ("greenhouse", "green"),
        ("lever", "blue"),
        ("ashby", "purple"),
        ("hn", "pink"),
        ("linkedin", "default"),
    ]
]

STAGE_OPTIONS = [
    {"name": s.value, "color": c}
    for s, c in [
        (Stage.SEED, "yellow"),
        (Stage.SERIES_A, "orange"),
        (Stage.SERIES_B, "red"),
        (Stage.SERIES_C, "pink"),
        (Stage.PUBLIC, "blue"),
        (Stage.UNKNOWN, "gray"),
    ]
]


def _schema() -> dict[str, Any]:
    return {
        "Title": {"title": {}},
        "Company": {"rich_text": {}},
        "Score": {"number": {"format": "number"}},
        "Why match": {"rich_text": {}},
        "Salary": {"rich_text": {}},
        "Stage": {"select": {"options": STAGE_OPTIONS}},
        "URL": {"url": {}},
        "Status": {"select": {"options": STATUS_OPTIONS}},
        "Source": {"select": {"options": SOURCE_OPTIONS}},
        "Found": {"date": {}},
    }


class NotionSink:
    def __init__(self, token: str, database_id: str | None = None) -> None:
        self.client = Client(auth=token)
        self.database_id = database_id

    def ensure_database(self, parent_page_id: str) -> str:
        """Create the database under the given page. Returns the database ID.
        Caller stores the returned ID in NOTION_DB_ID."""
        response = self.client.databases.create(
            parent={"type": "page_id", "page_id": parent_page_id},
            title=[{"type": "text", "text": {"content": DB_TITLE}}],
            properties=_schema(),
        )
        db_id = response["id"]
        self.database_id = db_id
        return str(db_id)

    def get_seen_urls(self) -> set[str]:
        """Return the set of URLs already in the database — the dedup key for the daily run."""
        if not self.database_id:
            raise RuntimeError("database_id not set")
        seen: set[str] = set()
        cursor: str | None = None
        while True:
            kwargs: dict[str, Any] = {"database_id": self.database_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            page = self.client.databases.query(**kwargs)
            for row in page.get("results", []):
                url = row.get("properties", {}).get("URL", {}).get("url")
                if url:
                    seen.add(url)
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor")
        return seen

    def upsert_postings(self, scored: list[ScoredPosting]) -> int:
        """Insert each scored posting as a new row with Status=New. Returns count inserted.
        Caller is responsible for dedup against get_seen_urls()."""
        if not self.database_id:
            raise RuntimeError("database_id not set")
        inserted = 0
        for sp in scored:
            try:
                self.client.pages.create(
                    parent={"database_id": self.database_id},
                    properties=_row_properties(sp),
                )
                inserted += 1
            except Exception:
                logger.exception("Failed to insert posting %s", sp.posting.url)
        return inserted

    def get_approved_rows(self) -> list[dict[str, Any]]:
        """Return rows where Status=Approved. Used by the local apply step (v1.2)."""
        if not self.database_id:
            raise RuntimeError("database_id not set")
        page = self.client.databases.query(
            database_id=self.database_id,
            filter={"property": "Status", "select": {"equals": "Approved"}},
            page_size=100,
        )
        return list(page.get("results", []))

    def update_status(self, page_id: str, status: str) -> None:
        """Update the Status select on a single row."""
        self.client.pages.update(
            page_id=page_id,
            properties={"Status": {"select": {"name": status}}},
        )


def _row_properties(sp: ScoredPosting) -> dict[str, Any]:
    p = sp.posting
    title = (p.title or "Untitled")[:TITLE_MAX]
    why = "\n".join(f"• {r}" for r in sp.score.reasons)
    props: dict[str, Any] = {
        "Title": {"title": [{"type": "text", "text": {"content": title}}]},
        "Company": {"rich_text": [{"type": "text", "text": {"content": p.company}}]},
        "Score": {"number": round(sp.score.value, 1)},
        "Why match": {"rich_text": [{"type": "text", "text": {"content": why}}]},
        "URL": {"url": canonical_url(p.url)},
        "Status": {"select": {"name": "New"}},
        "Source": {"select": {"name": p.source}},
        "Stage": {"select": {"name": p.stage.value}},
        "Found": {"date": {"start": p.found_at.date().isoformat()}},
    }
    if p.salary_text:
        props["Salary"] = {"rich_text": [{"type": "text", "text": {"content": p.salary_text}}]}
    return props
