"""Map common runtime exceptions to one-line user-facing messages.

The CLI wraps each command in friendly_errors() so users see actionable
hints instead of 30-line tracebacks. Use -v to opt back into stack traces.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

import typer

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def _hint_for(exc: Exception) -> str | None:
    """Return a remediation hint for known exception shapes, or None to fall through."""
    msg = str(exc)
    cls = type(exc).__name__

    # Notion 401 → integration probably not shared with the parent page
    if "Unauthorized" in msg or "unauthorized" in msg.lower():
        return (
            "Notion rejected the token. Check (1) NOTION_TOKEN is the integration's "
            "Internal Integration Secret, (2) the parent page is shared with the "
            "integration via that page's ••• menu → Connections."
        )

    # Notion 404 on database — usually a stale or placeholder DB ID
    if "object_not_found" in msg or ("404" in msg and "notion" in msg.lower()):
        return (
            "Notion couldn't find the database. Did you run `jobpilot init-notion` "
            "and paste the printed NOTION_DB_ID into .env? If you re-ran init-notion, "
            "make sure .env has the latest ID."
        )

    # Schema drift — properties don't exist on the DB
    if "is not a property that exists" in msg:
        return (
            "Notion DB schema doesn't match what jobpilot expects. The database may "
            "have been created with a different version of the tool. Run "
            "`jobpilot init-notion` to create a fresh DB and update NOTION_DB_ID."
        )

    # Required env var missing
    if cls == "RuntimeError" and "Required env var" in msg:
        return (
            "Add the missing variable to .env in this directory, or export it in "
            "your shell. Quick check: `cat .env`."
        )

    # Profile not found
    if cls == "FileNotFoundError" and "profile" in msg.lower():
        return "Run `cp profile.example.yaml profile.yaml` and edit the result."

    # Unresolved ${VAR} in profile
    if cls == "ValueError" and "references ${" in msg and "not set" in msg:
        return (
            "Either set that env var in .env, or use ${VAR:-default} in profile.yaml "
            "to provide a fallback (matches bash semantics)."
        )

    # Resume not found (tailor)
    if cls == "FileNotFoundError" and "resume" in msg.lower():
        return "Update profile.yaml's resume_path to point at an existing PDF or markdown file."

    # Anthropic rate limit / overload
    if "rate_limit" in msg.lower() or "overloaded" in msg.lower():
        return "Anthropic is rate-limiting or overloaded. Retry in a minute, or lower --limit."

    # Anthropic auth
    if "authentication" in msg.lower() or "invalid x-api-key" in msg.lower():
        return "Anthropic rejected ANTHROPIC_API_KEY. Check the key in .env."

    return None


def friendly_errors(verbose_var: str = "-v") -> Callable[[F], F]:
    """Decorator: render known exceptions as friendly one-liners and exit cleanly.

    Pass `-v` (or `--verbose`) on the command to fall back to a full traceback.
    The decorator inspects sys.argv as a last resort — Typer's `verbose` flag
    is plumbed through commands explicitly, but we also want this safety net.
    """

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            import sys

            verbose = kwargs.get("verbose", False) or any(
                a in sys.argv for a in ("-v", "--verbose")
            )
            try:
                return fn(*args, **kwargs)
            except (typer.Exit, KeyboardInterrupt):
                raise
            except Exception as e:
                if verbose:
                    raise
                from rich.console import Console

                console = Console(stderr=True)
                console.print(f"[red]Error:[/red] {type(e).__name__}: {e}")
                hint = _hint_for(e)
                if hint:
                    console.print(f"[dim]→ {hint}[/dim]")
                console.print("[dim]Run with -v for the full traceback.[/dim]")
                logger.debug("traceback suppressed", exc_info=e)
                raise typer.Exit(code=1) from None

        return wrapper  # type: ignore[return-value]

    return decorator
