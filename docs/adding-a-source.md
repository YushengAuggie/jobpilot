# Adding a job source

A source converts a remote API (or feed) into `JobPosting` objects. Each source is one file under `src/jobpilot/sources/` and gets registered into a global `REGISTRY` so the orchestrator can find it.

This walkthrough uses `hackernews.py` as the reference — read it alongside this doc.

## 1. Sketch the protocol

A `Source` is anything with these attributes:

```python
class Source(Protocol):
    name: str
    def list_jobs(self, profile: Profile, limit: int = 0) -> list[JobPosting]: ...
    def health(self) -> tuple[bool, str]: ...
```

`name` is one of the literals in `models.SourceName` (`yc`, `greenhouse`, `lever`, `ashby`, `hn`, `linkedin`). If you're adding a brand-new source type, add it to that union first.

## 2. Implement the source

Write your file at `src/jobpilot/sources/<name>.py`. The minimum is a fetch + a transform:

```python
from __future__ import annotations

import httpx

from jobpilot.models import JobPosting, Profile
from jobpilot.sources.base import register


class MySource:
    name = "mysource"

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def health(self) -> tuple[bool, str]:
        try:
            httpx.get("https://api.example.com/health", timeout=self.timeout).raise_for_status()
            return True, "ok"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def list_jobs(self, profile: Profile, limit: int = 0) -> list[JobPosting]:
        with httpx.Client(timeout=self.timeout) as client:
            r = client.get("https://api.example.com/jobs")
            r.raise_for_status()
            data = r.json()

        postings: list[JobPosting] = []
        for item in data.get("jobs", []):
            postings.append(JobPosting(
                title=item["title"],
                company=item["company"],
                url=item["url"],
                source="mysource",
                jd_text=item["description"],
                location=item.get("location"),
            ))
            if limit and len(postings) >= limit:
                break
        return postings


register(MySource())
```

**Notes:**
- Be defensive about missing fields — real APIs are messy. Use `.get()` with defaults or `try`/`except` per item, never let one bad row crash the whole fetch.
- Cap `jd_text` length only if the upstream is unbounded; the scorer caps at 6000 chars itself.
- Don't sleep, retry, or rate-limit inside `list_jobs` — let the orchestrator handle a broken source via `health()`.

## 3. Wire it into the registry

`sources/__init__.py` imports each source module so its `register(...)` call runs. Add yours:

```python
from jobpilot.sources import ats_boards, hackernews, mysource  # noqa: F401
```

That's all the orchestrator needs to discover it.

## 4. Add a replay test

Tests are split into layers — `unit`, `replay`, and `live`. Your source needs at minimum a `replay` test that mocks the HTTP boundary with `respx`.

```python
# tests/test_mysource.py
import httpx
import pytest
import respx

from jobpilot.models import Profile
from jobpilot.sources.mysource import MySource


def _profile() -> Profile:
    return Profile.model_validate({
        "name": "T", "resume_path": "/tmp/r.pdf",
        "target_roles": ["X"], "strengths": ["y"], "salary_min_usd": 0,
        "stages": ["seed"], "locations": ["Remote"],
        "ats_boards": {}, "notion": {"database_id": "x"},
    })


@pytest.mark.replay
@respx.mock
def test_my_source_fetches_and_maps() -> None:
    respx.get("https://api.example.com/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [
            {"title": "Eng", "company": "X", "url": "u", "description": "work"}
        ]})
    )
    postings = MySource().list_jobs(_profile())
    assert len(postings) == 1
    assert postings[0].source == "mysource"
```

Optionally add a `@pytest.mark.live` smoke test that hits the real API — these run nightly only, not on PR.

## 5. Configure (if needed)

If your source needs per-user config (company slugs, region, etc.), extend `Profile` in `models.py` and update `profile.example.yaml` with documented placeholders. Keep config additions backward-compatible — default to empty so existing profiles still validate.

## Checklist

- [ ] `src/jobpilot/sources/<name>.py` implements the `Source` protocol
- [ ] `register(<YourSource>())` at module bottom
- [ ] Import added in `sources/__init__.py`
- [ ] `tests/test_<name>.py` with at least one `@pytest.mark.replay` test
- [ ] `profile.example.yaml` updated if config needed
- [ ] `README.md` "What it does" section updated to mention the new source
