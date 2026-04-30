"""jobpilot CLI."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from jobpilot._jd_fetch import fetch_jd_text
from jobpilot.config import load_profile, require_env
from jobpilot.models import JobPosting
from jobpilot.notion_sink import NotionSink
from jobpilot.pipeline import run_daily as run_pipeline
from jobpilot.tailor import Tailorer

app = typer.Typer(help="Daily AI-curated job shortlist + tailored applications.")
console = Console()
logger = logging.getLogger(__name__)


def _slug(text: str, max_chars: int = 40) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:max_chars] or "untitled"


def _row_field(row: dict[str, Any], name: str, kind: str) -> str | None:
    """Extract a Notion property value from a row dict. Returns None when missing."""
    prop = row.get("properties", {}).get(name, {})
    if kind == "title":
        items = prop.get("title", [])
        return items[0].get("plain_text") if items else None
    if kind == "rich_text":
        items = prop.get("rich_text", [])
        return items[0].get("plain_text") if items else None
    if kind == "url":
        return prop.get("url")
    if kind == "select":
        sel = prop.get("select")
        return sel.get("name") if sel else None
    return None


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.command("init-notion")
def init_notion(
    parent_page_id: str = typer.Option(
        ...,
        "--parent-page-id",
        help="Notion page ID where the database will be created. Share that page with your integration first.",
    ),
) -> None:
    """Create the jobpilot database under the given Notion page. Prints the database ID."""
    sink = NotionSink(token=require_env("NOTION_TOKEN"))
    db_id = sink.ensure_database(parent_page_id=parent_page_id)
    console.print(f"[green]Created database:[/green] {db_id}")
    console.print("\n[bold]Next:[/bold] add this to your .env:\n")
    console.print(f"NOTION_DB_ID={db_id}")


@app.command("parse-resume")
def parse_resume(
    path: Path = typer.Argument(..., help="Path to your resume PDF or markdown."),
    profile_out: Path = typer.Option(
        Path("profile.yaml"), "--out", help="Where to write the profile.yaml seed."
    ),
) -> None:
    """Seed profile.yaml from profile.example.yaml, pointing it at your resume.
    Edit the resulting file before running --run-daily."""
    if not path.exists():
        console.print(f"[red]Resume not found:[/red] {path}")
        raise typer.Exit(code=1)
    if profile_out.exists():
        console.print(f"[yellow]{profile_out} already exists. Refusing to overwrite.[/yellow]")
        raise typer.Exit(code=1)

    example = Path("profile.example.yaml")
    if not example.exists():
        console.print("[red]profile.example.yaml not found in cwd. Run from repo root.[/red]")
        raise typer.Exit(code=1)

    text = example.read_text()
    text = text.replace("~/Documents/resume.pdf", str(path.expanduser().resolve()))
    profile_out.write_text(text)
    console.print(f"[green]Wrote {profile_out}.[/green] Edit it (target_roles, salary, etc.) then re-run.")


@app.command("run-daily")
def run_daily(
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip Notion writes."),
    no_score: bool = typer.Option(
        False,
        "--no-score",
        help="Skip Claude scoring entirely (implies --dry-run). Verifies source connectivity without an Anthropic key.",
    ),
    limit: int = typer.Option(0, "--limit", help="Cap postings per source (0 = no cap)."),
    sources: list[str] = typer.Option(
        None, "--source", help="Restrict to specific sources (repeatable)."
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Fetch, score, and upsert today's shortlist into Notion."""
    _setup_logging(verbose)
    profile = load_profile()

    summary = run_pipeline(
        profile,
        dry_run=dry_run,
        skip_scoring=no_score,
        limit_per_source=limit,
        sources=sources or None,
    )

    table = Table(title="Daily run", show_header=False)
    table.add_row("Sources OK", ", ".join(summary.sources_ok) or "(none)")
    if summary.sources_broken:
        broken = "; ".join(f"{k}={v}" for k, v in summary.sources_broken.items())
        table.add_row("Sources broken", f"[red]{broken}[/red]")
    table.add_row("Fetched", str(summary.fetched))
    table.add_row("New (post-dedup)", str(summary.new_after_dedup))
    table.add_row("Scored", str(summary.scored))
    table.add_row("Passed filters", str(summary.passed_filters))
    table.add_row("Upserted to Notion", str(summary.upserted) if not dry_run else "(dry-run)")
    console.print(table)

    if summary.samples:
        console.print("\n[bold]Top picks:[/bold]")
        sample_table = Table()
        sample_table.add_column("Score", justify="right")
        sample_table.add_column("Title")
        sample_table.add_column("Company")
        sample_table.add_column("Source")
        for sp in summary.samples:
            sample_table.add_row(
                f"{sp.score.value:.1f}",
                sp.posting.title[:60],
                sp.posting.company[:30],
                sp.posting.source,
            )
        console.print(sample_table)


@app.command("tailor")
def tailor(
    limit: int = typer.Option(0, "--limit", help="Cap rows processed (0 = all Approved rows)."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Generate materials but don't update Notion status."
    ),
    output_dir: Path = typer.Option(
        Path("applications"), "--out", help="Where to save tailored materials."
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Generate a tailored resume + cover letter for every Notion row marked Approved.

    Materials are saved under <out>/<company>-<slug>/. On success, the row's Status
    flips to Materials-Ready (skipped under --dry-run).
    """
    _setup_logging(verbose)
    profile = load_profile()
    sink = NotionSink(token=require_env("NOTION_TOKEN"), database_id=profile.notion.database_id)
    tailorer = Tailorer()

    rows = sink.get_approved_rows()
    if limit > 0:
        rows = rows[:limit]

    if not rows:
        console.print(
            "[yellow]No Approved rows in Notion. Flip rows from New to Approved to tailor them.[/yellow]"
        )
        return

    console.print(f"[green]Tailoring {len(rows)} approved postings...[/green]\n")
    table = Table()
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("Output", overflow="fold")
    table.add_column("Status")

    for row in rows:
        title = _row_field(row, "Title", "title") or "(untitled)"
        company = _row_field(row, "Company", "rich_text") or "(unknown)"
        url = _row_field(row, "URL", "url")
        if not url:
            table.add_row(company, title[:50], "—", "[red]no URL[/red]")
            continue

        try:
            jd_text = fetch_jd_text(url)
        except Exception as e:
            logger.exception("Failed to fetch JD for %s", url)
            table.add_row(company, title[:50], "—", f"[red]fetch failed: {type(e).__name__}[/red]")
            continue

        posting = JobPosting(
            title=title,
            company=company,
            url=url,
            source="hn",  # exact source not relevant to tailoring
            jd_text=jd_text,
        )

        try:
            resume_md, cover_md = tailorer.tailor(profile, posting)
        except Exception as e:
            logger.exception("Tailoring failed for %s", url)
            table.add_row(company, title[:50], "—", f"[red]tailor failed: {type(e).__name__}[/red]")
            continue

        slug = f"{_slug(company)}-{_slug(title)}"
        target = output_dir / slug
        target.mkdir(parents=True, exist_ok=True)
        (target / "resume.md").write_text(resume_md)
        (target / "cover_letter.md").write_text(cover_md)

        if not dry_run:
            try:
                sink.update_status(row["id"], "Materials-Ready")
                status_label = "[green]Materials-Ready[/green]"
            except Exception:
                logger.exception("Failed to update Notion status for %s", url)
                status_label = "[yellow]saved, status update failed[/yellow]"
        else:
            status_label = "[cyan]dry-run (no status update)[/cyan]"

        table.add_row(company[:30], title[:50], str(target), status_label)

    console.print(table)


@app.command("apply-pending")
def apply_pending(
    limit: int = typer.Option(0, "--limit", help="Cap rows processed (0 = all)."),
    output_dir: Path = typer.Option(
        Path("applications"), "--out", help="Where tailored materials live."
    ),
    headless: bool = typer.Option(
        False, "--headless", help="Run browser headlessly (default: visible so you can submit)."
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Open each Materials-Ready posting in a browser with the form pre-filled.

    For each row whose Status=Materials-Ready: render resume.md → resume.pdf via
    pandoc, open the application URL in Chromium, auto-fill the fields we can detect
    (Greenhouse / Lever / Ashby), wait for you to review and submit, then update
    Notion Status to Submitted.

    Requires Playwright. First-time setup:
        uv sync --extra apply
        uv run playwright install chromium
    """
    _setup_logging(verbose)
    profile = load_profile()
    sink = NotionSink(token=require_env("NOTION_TOKEN"), database_id=profile.notion.database_id)

    rows = [
        row
        for row in sink.client.databases.query(
            database_id=profile.notion.database_id,
            filter={"property": "Status", "select": {"equals": "Materials-Ready"}},
            page_size=100,
        ).get("results", [])
    ]
    if limit > 0:
        rows = rows[:limit]

    if not rows:
        console.print(
            "[yellow]No Materials-Ready rows. Run `jobpilot tailor` first to generate "
            "materials, or flip Approved rows after tailoring.[/yellow]"
        )
        return

    # Lazy import so that running --help / other commands doesn't fail when
    # the [apply] extra isn't installed.
    from jobpilot.apply import Applicator, render_resume_pdf

    console.print(f"[green]Opening {len(rows)} Materials-Ready postings...[/green]\n")

    with Applicator(headless=headless) as appl:
        for row in rows:
            title = _row_field(row, "Title", "title") or "(untitled)"
            company = _row_field(row, "Company", "rich_text") or "(unknown)"
            url = _row_field(row, "URL", "url")
            if not url:
                console.print(f"[red]{company} — {title}: no URL on row[/red]")
                continue

            slug = f"{_slug(company)}-{_slug(title)}"
            slug_dir = output_dir / slug
            resume_md = slug_dir / "resume.md"
            cover_md = slug_dir / "cover_letter.md"

            if not resume_md.exists() or not cover_md.exists():
                console.print(
                    f"[red]{company} — {title}: missing materials in {slug_dir}. "
                    f"Re-run `jobpilot tailor` for this row.[/red]"
                )
                continue

            resume_pdf = render_resume_pdf(resume_md)
            if resume_pdf is None:
                console.print(
                    f"[yellow]{company} — {title}: pandoc unavailable; resume upload "
                    f"will need manual selection. Materials still at {slug_dir}.[/yellow]"
                )

            console.print(f"\n[cyan]{company}[/cyan] — {title}")
            console.print(f"  URL: {url}")
            console.print(f"  Materials: {slug_dir}")
            try:
                ats = appl.apply_to(url, profile, resume_pdf, cover_md)
                console.print(f"  ATS: [green]{ats}[/green] (auto-fill attempted)")
            except Exception as e:
                logger.exception("Applicator failed for %s", url)
                console.print(f"  [red]Auto-fill failed: {type(e).__name__}: {e}[/red]")
                console.print("  Page should still be open — fill manually if it loaded.")

            response = typer.prompt(
                "  Submitted? [y]es / [N]o / [s]kip", default="N", show_default=False
            ).lower().strip()
            if response.startswith("y"):
                try:
                    sink.update_status(row["id"], "Submitted")
                    console.print("  [green]Status → Submitted[/green]")
                except Exception:
                    logger.exception("Notion status update failed")
                    console.print("  [yellow]Saved locally; Notion update failed[/yellow]")
            elif response.startswith("s"):
                console.print("  [dim]Skipped — Status stays Materials-Ready[/dim]")
            else:
                console.print("  [dim]Marked as not-yet-submitted — Status stays Materials-Ready[/dim]")


if __name__ == "__main__":
    app()
