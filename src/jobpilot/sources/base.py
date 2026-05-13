"""Source protocol + registry. Each source is one file under src/jobpilot/sources/."""

from __future__ import annotations

from typing import Protocol

from jobpilot.models import JobPosting, Profile


class Source(Protocol):
    """A job posting source. Stateless; can be re-instantiated per run.

    Failures during list_jobs propagate as exceptions; the pipeline catches them
    per-source so one broken source doesn't kill the run.
    """

    name: str

    def list_jobs(self, profile: Profile, limit: int = 0) -> list[JobPosting]:
        """Fetch postings. limit=0 means no cap."""
        ...


REGISTRY: dict[str, Source] = {}


def register(source: Source) -> Source:
    """Register a source instance for the orchestrator to discover.
    Idempotent — re-registering the same name overwrites (fine for tests + reloads)."""
    REGISTRY[source.name] = source
    return source
