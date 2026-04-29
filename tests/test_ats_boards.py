"""ATS-board source tests (Greenhouse / Lever / Ashby) — replay-mocked."""

from __future__ import annotations

import httpx
import pytest
import respx

from jobpilot.models import Profile
from jobpilot.sources.ats_boards import AshbySource, GreenhouseSource, LeverSource


def _profile(**boards: list[str]) -> Profile:
    return Profile.model_validate(
        {
            "name": "T",
            "resume_path": "/tmp/r.pdf",
            "target_roles": ["Eng"],
            "strengths": ["x"],
            "salary_min_usd": 0,
            "stages": ["seed"],
            "locations": ["Remote"],
            "ats_boards": boards,
            "notion": {"database_id": "x"},
        }
    )


@pytest.mark.replay
class TestGreenhouse:
    @respx.mock
    def test_fetches_and_maps(self) -> None:
        respx.get("https://boards-api.greenhouse.io/v1/boards/anthropic/jobs?content=true").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "title": "Staff Eng",
                            "location": {"name": "Remote"},
                            "content": "<p>distributed systems</p>",
                            "absolute_url": "https://boards.greenhouse.io/anthropic/jobs/1",
                        },
                        {
                            "title": "PM",
                            "location": {"name": "SF"},
                            "content": "<p>roadmap</p>",
                            "absolute_url": "https://boards.greenhouse.io/anthropic/jobs/2",
                        },
                    ]
                },
            )
        )

        postings = GreenhouseSource().list_jobs(_profile(greenhouse=["anthropic"]))

        assert len(postings) == 2
        assert postings[0].title == "Staff Eng"
        assert postings[0].source == "greenhouse"
        assert postings[0].company == "anthropic"
        assert "distributed systems" in postings[0].jd_text
        assert postings[0].location == "Remote"

    @respx.mock
    def test_respects_limit(self) -> None:
        respx.get("https://boards-api.greenhouse.io/v1/boards/x/jobs?content=true").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {"title": f"Job {i}", "location": {"name": "X"}, "content": "x", "absolute_url": f"u/{i}"}
                        for i in range(10)
                    ]
                },
            )
        )
        postings = GreenhouseSource().list_jobs(_profile(greenhouse=["x"]), limit=3)
        assert len(postings) == 3

    @respx.mock
    def test_handles_404_gracefully(self) -> None:
        respx.get("https://boards-api.greenhouse.io/v1/boards/missing/jobs?content=true").mock(
            return_value=httpx.Response(404, json={})
        )
        postings = GreenhouseSource().list_jobs(_profile(greenhouse=["missing"]))
        assert postings == []


@pytest.mark.replay
class TestLever:
    @respx.mock
    def test_fetches_and_maps(self) -> None:
        respx.get("https://api.lever.co/v0/postings/figma?mode=json").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "abc",
                        "text": "Senior Engineer",
                        "categories": {"location": "SF", "team": "Eng"},
                        "descriptionPlain": "design systems work",
                        "hostedUrl": "https://jobs.lever.co/figma/abc",
                    }
                ],
            )
        )

        postings = LeverSource().list_jobs(_profile(lever=["figma"]))

        assert len(postings) == 1
        assert postings[0].title == "Senior Engineer"
        assert postings[0].source == "lever"
        assert postings[0].company == "figma"
        assert "design systems" in postings[0].jd_text
        assert postings[0].location == "SF"


@pytest.mark.replay
class TestAshby:
    @respx.mock
    def test_fetches_and_maps(self) -> None:
        respx.get("https://api.ashbyhq.com/posting-api/job-board/linear").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": "j1",
                            "title": "Tech Lead",
                            "location": "Remote",
                            "descriptionHtml": "<p>perf-critical UI</p>",
                            "jobUrl": "https://jobs.ashbyhq.com/linear/j1",
                        }
                    ]
                },
            )
        )

        postings = AshbySource().list_jobs(_profile(ashby=["linear"]))

        assert len(postings) == 1
        assert postings[0].title == "Tech Lead"
        assert postings[0].source == "ashby"
        assert "perf-critical UI" in postings[0].jd_text
