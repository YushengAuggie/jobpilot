"""Daily run orchestration: fetch → dedup → score → filter → upsert."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from jobpilot.config import require_env
from jobpilot.filters import canonical_url, has_dealbreaker, passes_filters
from jobpilot.models import JobPosting, Profile, ScoredPosting
from jobpilot.notion_sink import NotionSink
from jobpilot.score import Scorer
from jobpilot.sources import REGISTRY

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    sources_ok: list[str]
    sources_broken: dict[str, str]
    fetched: int
    new_after_dedup: int
    scored: int
    passed_filters: int
    upserted: int
    samples: list[ScoredPosting]


def run_daily(
    profile: Profile,
    *,
    dry_run: bool = False,
    skip_scoring: bool = False,
    limit_per_source: int = 0,
    sources: list[str] | None = None,
    sink: NotionSink | None = None,
    scorer: Scorer | None = None,
) -> RunSummary:
    """Execute the full daily pipeline. Returns a RunSummary suitable for logging.

    - dry_run: skip Notion writes; useful for first-time verification
    - skip_scoring: skip Claude scoring entirely; implies dry_run. Useful for verifying
      source connectivity before paying for Anthropic. All postings get a placeholder
      score of 0 with a "scoring skipped" reason.
    - limit_per_source: cap postings per source (0 = no cap)
    - sources: explicit list of source names; defaults to all registered
    """
    selected = list(sources) if sources else list(REGISTRY.keys())

    if skip_scoring:
        dry_run = True  # never write unscored postings to Notion

    # Defer NotionSink construction. With dry_run=True we never call get_seen_urls
    # or upsert_postings, so requiring NOTION_TOKEN at that point would force the
    # user to set up Notion just to smoke-test sources. Build only when needed.
    needs_notion = not dry_run
    if sink is None and needs_notion:
        sink = NotionSink(
            token=require_env("NOTION_TOKEN"),
            database_id=profile.notion.database_id,
        )
    scorer = scorer or (None if skip_scoring else Scorer())

    seen_urls: set[str] = set()
    if not dry_run and sink is not None:
        try:
            seen_urls = {canonical_url(u) for u in sink.get_seen_urls()}
        except Exception:
            logger.exception("Failed to load seen URLs from Notion; treating run as fresh")

    sources_ok: list[str] = []
    sources_broken: dict[str, str] = {}
    fetched: list[JobPosting] = []

    for name in selected:
        source = REGISTRY.get(name)
        if source is None:
            sources_broken[name] = "not registered"
            continue
        try:
            postings = source.list_jobs(profile, limit=limit_per_source)
        except Exception as e:
            logger.exception("Source %s failed", name)
            sources_broken[name] = f"{type(e).__name__}: {e}"
            continue
        sources_ok.append(name)
        fetched.extend(postings)
        logger.info("source %s fetched %d postings", name, len(postings))

    # Dedup against Notion + within this run
    new_postings: list[JobPosting] = []
    seen_in_run: set[str] = set()
    for p in fetched:
        url = canonical_url(p.url)
        if url in seen_urls or url in seen_in_run:
            continue
        seen_in_run.add(url)
        new_postings.append(p)

    # Cheap pre-filter: drop dealbreaker keyword hits BEFORE paying for Claude
    # scoring. An attacker who plants "crypto" in HN comments could otherwise
    # burn API spend at our expense. Skip when scoring is skipped — placeholder
    # scores aren't meaningful and the user is just inspecting source output.
    if not skip_scoring and profile.dealbreakers:
        before = len(new_postings)
        new_postings = [
            p for p in new_postings
            if not has_dealbreaker(p.jd_text, profile.dealbreakers)
        ]
        dropped = before - len(new_postings)
        if dropped:
            logger.info("pre-filtered %d posting(s) on dealbreaker match", dropped)

    # Score (or stub when skip_scoring is set)
    scored: list[ScoredPosting] = []
    if skip_scoring:
        from jobpilot.models import Score

        placeholder = Score(value=0, reasons=["scoring skipped"])
        scored = [ScoredPosting(posting=p, score=placeholder) for p in new_postings]
    else:
        assert scorer is not None
        for p in new_postings:
            try:
                score = scorer.score(profile, p)
            except Exception:
                logger.exception("Scoring failed for %s; skipping", p.url)
                continue
            scored.append(ScoredPosting(posting=p, score=score))

    # Filter (skipped when scoring is skipped — no real scores to filter on)
    kept: list[ScoredPosting] = scored if skip_scoring else []
    if not skip_scoring:
        for sp in scored:
            passed, reason = passes_filters(sp, profile)
            if passed:
                kept.append(sp)
            else:
                logger.debug("dropped %s — %s", sp.posting.url, reason)

    # Rank + cap
    kept.sort(key=lambda sp: sp.score.value, reverse=True)
    kept = kept[: profile.daily_limit]

    upserted = 0
    if not dry_run and kept and sink is not None:
        upserted = sink.upsert_postings(kept)

    return RunSummary(
        sources_ok=sources_ok,
        sources_broken=sources_broken,
        fetched=len(fetched),
        new_after_dedup=len(new_postings),
        scored=len(scored),
        passed_filters=len(kept),
        upserted=upserted,
        samples=kept[:5],
    )
