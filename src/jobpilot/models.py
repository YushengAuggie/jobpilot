"""Domain models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Stage(StrEnum):
    SEED = "seed"
    SERIES_A = "series-a"
    SERIES_B = "series-b"
    SERIES_C = "series-c"
    PUBLIC = "public"
    UNKNOWN = "unknown"


SourceName = Literal["yc", "greenhouse", "lever", "ashby", "hn", "linkedin"]


class JobPosting(BaseModel):
    title: str
    company: str
    url: str
    source: SourceName
    jd_text: str
    location: str | None = None
    salary_text: str | None = None
    stage: Stage = Stage.UNKNOWN
    found_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Score(BaseModel):
    value: float = Field(ge=0, le=10)
    reasons: list[str]


class NotionConfig(BaseModel):
    database_id: str


class AtsBoards(BaseModel):
    greenhouse: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)
    ashby: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    name: str
    resume_path: str
    target_roles: list[str]
    strengths: list[str]
    salary_min_usd: int = Field(ge=0)
    stages: list[Stage]
    locations: list[str]
    dealbreakers: list[str] = Field(default_factory=list)
    ats_boards: AtsBoards = Field(default_factory=AtsBoards)
    notion: NotionConfig
    score_threshold: float = Field(ge=0, le=10, default=6)
    daily_limit: int = Field(ge=1, default=25)


class ScoredPosting(BaseModel):
    """A posting paired with its Claude score. Used for filtering and Notion upsert."""

    posting: JobPosting
    score: Score
