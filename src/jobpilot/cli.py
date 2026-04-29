"""jobpilot CLI."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from jobpilot.config import load_profile, require_env
from jobpilot.notion_sink import NotionSink
from jobpilot.pipeline import run_daily as run_pipeline

app = typer.Typer(help="Daily AI-curated job shortlist + tailored applications.")
console = Console()


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


@app.command("apply-pending")
def apply_pending() -> None:
    """Tailor materials and pre-fill applications for Notion rows marked Approved (v1.2)."""
    console.print("[yellow]apply-pending: not yet implemented (planned for v1.2)[/yellow]")


if __name__ == "__main__":
    app()
