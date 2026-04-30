"""Open application URLs in a controlled browser and pre-fill what we can.

The auto-fill is best-effort — Greenhouse/Lever/Ashby selectors are documented
but they drift over time. Each field tries multiple selectors and logs a
warning when none match; the browser stays open either way so the user can
fill in whatever wasn't auto-detected and submit manually.

Playwright is an optional dependency. Install via:

    uv sync --extra apply
    uv run playwright install chromium

ATS detection is based on hostname. URLs that don't match a known provider
fall through with the page opened but no auto-fill attempted.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from jobpilot.models import Profile

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)

ATSName = Literal["greenhouse", "lever", "ashby", "unknown"]

# Per-ATS selector lists. First match wins; others are fallbacks for when the
# ATS tweaks markup. Keep alternatives broad enough to survive minor changes.
ATS_CONFIGS: dict[str, dict[str, list[str]]] = {
    "greenhouse": {
        "first_name": [
            "input#first_name",
            "input[name='job_application[first_name]']",
            "input[autocomplete='given-name']",
        ],
        "last_name": [
            "input#last_name",
            "input[name='job_application[last_name]']",
            "input[autocomplete='family-name']",
        ],
        "email": [
            "input#email",
            "input[name='job_application[email]']",
            "input[type='email']",
        ],
        "phone": [
            "input#phone",
            "input[name='job_application[phone]']",
            "input[type='tel']",
        ],
        "resume_upload": [
            "input[type='file'][id*='resume' i]",
            "input[name='job_application[resume_text_file]']",
            "input[type='file']",
        ],
        "cover_letter_textarea": [
            "textarea#cover_letter",
            "textarea[name*='cover' i]",
        ],
    },
    "lever": {
        "full_name": [
            "input[name='name']",
            "input[autocomplete='name']",
        ],
        "email": [
            "input[name='email']",
            "input[type='email']",
        ],
        "phone": [
            "input[name='phone']",
            "input[type='tel']",
        ],
        "resume_upload": [
            "input[type='file']",
        ],
    },
    "ashby": {
        "full_name": [
            "input[autocomplete='name']",
            "input[placeholder*='name' i]",
        ],
        "email": [
            "input[type='email']",
            "input[autocomplete='email']",
        ],
        "phone": [
            "input[type='tel']",
            "input[autocomplete='tel']",
        ],
        "resume_upload": [
            "input[type='file']",
        ],
    },
}


def detect_ats(url: str) -> ATSName:
    """Return the ATS provider for a URL based on hostname.

    Matches the hostname exactly or as a strict suffix (preceded by a dot) to
    avoid classifying attacker-controlled hosts like ``greenhouse.io.evil.com``
    as a trusted ATS — that would route auto-filled personal data into a
    phishing page.
    """
    host = (urlparse(url).hostname or "").lower()
    for domain, ats in (
        ("greenhouse.io", "greenhouse"),
        ("lever.co", "lever"),
        ("ashbyhq.com", "ashby"),
    ):
        if host == domain or host.endswith("." + domain):
            return ats  # type: ignore[return-value]
    return "unknown"


def render_resume_pdf(resume_md: Path) -> Path | None:
    """Render markdown to PDF via pandoc. Returns the PDF path, or None if pandoc isn't
    installed or rendering fails. Caller should fall back to manual upload in that case."""
    if not shutil.which("pandoc"):
        logger.info("pandoc not installed; skipping PDF render. brew install pandoc to enable.")
        return None
    pdf_path = resume_md.with_suffix(".pdf")
    try:
        subprocess.run(
            ["pandoc", str(resume_md), "-o", str(pdf_path)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return pdf_path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        stderr = getattr(e, "stderr", b"")
        logger.warning("pandoc failed for %s: %s", resume_md, stderr.decode() if stderr else e)
        return None


def _try_fill(
    page: Page, selectors: list[str], value: str, field_name: str, *, timeout_ms: int = 2000
) -> bool:
    """Try each selector until one matches a visible element; fill it. Returns True on success."""
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            locator.wait_for(state="visible", timeout=timeout_ms)
            locator.fill(value)
            logger.info("filled %s via %s", field_name, sel)
            return True
        except Exception:
            continue
    logger.info("could not fill %s — no selector matched", field_name)
    return False


def _try_upload(
    page: Page,
    selectors: list[str],
    file_path: Path,
    field_name: str,
    *,
    timeout_ms: int = 2000,
) -> bool:
    """Wait for each candidate selector to appear in the DOM, then set files.

    Uses ``state="attached"`` rather than ``state="visible"`` because file
    inputs on most ATS pages are visually hidden behind a styled label — they
    still accept set_input_files when only attached. Without this wait, SPA
    pages that mount the form async would silently miss the upload.
    """
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            locator.wait_for(state="attached", timeout=timeout_ms)
            locator.set_input_files(str(file_path))
            logger.info("uploaded %s via %s (%s)", field_name, sel, file_path)
            return True
        except Exception:
            continue
    logger.info("could not upload %s", field_name)
    return False


def _fill_form(
    page: Page,
    profile: Profile,
    ats: ATSName,
    resume_pdf: Path | None,
    cover_letter_md: Path | None,
) -> None:
    """Generic dispatcher that uses ATS_CONFIGS to try selectors per field."""
    if ats not in ATS_CONFIGS:
        return
    cfg = ATS_CONFIGS[ats]

    # Name fields — Greenhouse splits first/last; Lever/Ashby take a single full name.
    if "first_name" in cfg:
        first, *rest = profile.name.split(" ", 1)
        last = rest[0] if rest else ""
        _try_fill(page, cfg["first_name"], first, "first_name")
        if last:
            _try_fill(page, cfg["last_name"], last, "last_name")
    elif "full_name" in cfg:
        _try_fill(page, cfg["full_name"], profile.name, "full_name")

    if profile.email and "email" in cfg:
        _try_fill(page, cfg["email"], profile.email, "email")
    if profile.phone and "phone" in cfg:
        _try_fill(page, cfg["phone"], profile.phone, "phone")

    if resume_pdf and resume_pdf.exists() and "resume_upload" in cfg:
        _try_upload(page, cfg["resume_upload"], resume_pdf, "resume")

    if cover_letter_md and cover_letter_md.exists() and "cover_letter_textarea" in cfg:
        cover_text = cover_letter_md.read_text()
        _try_fill(page, cfg["cover_letter_textarea"], cover_text, "cover_letter")


class Applicator:
    """Context-managed Playwright session that pre-fills application forms.

    Usage:
        with Applicator() as app:
            ats = app.apply_to(url, profile, resume_pdf, cover_letter_md)
            input("Press ENTER after submitting...")
    """

    def __init__(self, headless: bool = False, slow_mo_ms: int = 200) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "Playwright not installed. Run: uv sync --extra apply "
                "&& uv run playwright install chromium"
            ) from e
        self._start = sync_playwright
        self._pw = None
        self._browser = None
        self._context = None
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms

    def __enter__(self) -> Applicator:
        self._pw = self._start().start()
        self._browser = self._pw.chromium.launch(headless=self.headless, slow_mo=self.slow_mo_ms)
        self._context = self._browser.new_context()
        return self

    def __exit__(self, *exc: object) -> None:
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._pw, "stop", None),
        ):
            if closer is not None:
                with contextlib.suppress(Exception):
                    closer()

    def apply_to(
        self,
        url: str,
        profile: Profile,
        resume_pdf: Path | None,
        cover_letter_md: Path | None,
    ) -> ATSName:
        """Open the URL, detect the ATS, and pre-fill what we can. Returns the ATS detected.
        The page is left open — caller is responsible for waiting until the user submits."""
        if self._context is None:
            raise RuntimeError("Applicator must be used as a context manager")
        page = self._context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        ats = detect_ats(url)
        if ats == "unknown":
            logger.info("Unknown ATS for %s — opened the page without auto-fill", url)
            return ats

        _fill_form(page, profile, ats, resume_pdf, cover_letter_md)
        return ats
