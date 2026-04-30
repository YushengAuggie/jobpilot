"""Tests for the v1.2 apply-pending step. Mocks Playwright — no real browser."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jobpilot.apply import (
    ATS_CONFIGS,
    Applicator,
    _fill_form,
    _try_fill,
    _try_upload,
    detect_ats,
    render_resume_pdf,
)
from jobpilot.models import Profile

pytestmark = pytest.mark.unit


def _profile(**overrides: object) -> Profile:
    base = {
        "name": "Yusheng D.",
        "resume_path": "/tmp/r.pdf",
        "target_roles": ["Staff Engineer"],
        "strengths": ["python"],
        "salary_min_usd": 0,
        "stages": ["seed"],
        "locations": ["Remote"],
        "ats_boards": {},
        "notion": {"database_id": "x"},
        "email": "yusheng@example.com",
        "phone": "+1-555-555-5555",
    }
    base.update(overrides)
    return Profile.model_validate(base)


class TestDetectAts:
    @pytest.mark.parametrize(
        "url, expected",
        [
            ("https://boards.greenhouse.io/anthropic/jobs/12345", "greenhouse"),
            ("https://boards-api.greenhouse.io/anthropic/jobs/12345", "greenhouse"),
            ("https://jobs.lever.co/figma/abc-123", "lever"),
            ("https://api.lever.co/v0/postings/figma", "lever"),
            ("https://jobs.ashbyhq.com/linear/job-id", "ashby"),
            ("https://www.linkedin.com/jobs/view/12345", "unknown"),
            ("https://example.com/careers/eng", "unknown"),
            ("not a url", "unknown"),
            ("", "unknown"),
        ],
    )
    def test_detects_provider(self, url: str, expected: str) -> None:
        assert detect_ats(url) == expected


class TestRenderResumePdf:
    def test_returns_none_when_pandoc_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("jobpilot.apply.shutil.which", lambda _: None)
        md = tmp_path / "r.md"
        md.write_text("# Test")
        assert render_resume_pdf(md) is None

    def test_runs_pandoc_when_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        md = tmp_path / "r.md"
        md.write_text("# Test")
        expected_pdf = md.with_suffix(".pdf")

        monkeypatch.setattr("jobpilot.apply.shutil.which", lambda _: "/usr/bin/pandoc")
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            expected_pdf.write_bytes(b"%PDF-1.4 fake")
            return MagicMock(returncode=0)

        monkeypatch.setattr("jobpilot.apply.subprocess.run", fake_run)

        result = render_resume_pdf(md)
        assert result == expected_pdf
        assert "pandoc" in captured["cmd"][0]
        assert str(md) in captured["cmd"]

    def test_returns_none_when_pandoc_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess as sp

        md = tmp_path / "r.md"
        md.write_text("# Test")

        monkeypatch.setattr("jobpilot.apply.shutil.which", lambda _: "/usr/bin/pandoc")

        def fake_run(*args, **kwargs):
            raise sp.CalledProcessError(1, args[0], stderr=b"pandoc explosion")

        monkeypatch.setattr("jobpilot.apply.subprocess.run", fake_run)

        assert render_resume_pdf(md) is None


class TestTryFill:
    def test_fills_first_visible_match(self) -> None:
        page = MagicMock()
        loc1 = MagicMock()
        loc1.wait_for.side_effect = Exception("not visible")
        loc2 = MagicMock()  # this one succeeds
        page.locator.return_value.first = loc1
        # Make page.locator return different locators on different calls
        locators = iter([MagicMock(first=loc1), MagicMock(first=loc2)])
        page.locator.side_effect = lambda sel: next(locators)

        result = _try_fill(page, ["sel-1", "sel-2"], "value", "field")

        assert result is True
        loc2.fill.assert_called_once_with("value")

    def test_returns_false_when_no_selector_matches(self) -> None:
        page = MagicMock()
        loc = MagicMock()
        loc.wait_for.side_effect = Exception("never visible")
        page.locator.return_value.first = loc

        assert _try_fill(page, ["sel-1", "sel-2"], "value", "field") is False
        loc.fill.assert_not_called()


class TestTryUpload:
    def test_uploads_when_selector_matches(self, tmp_path: Path) -> None:
        page = MagicMock()
        loc = MagicMock()
        loc.count.return_value = 1
        page.locator.return_value.first = loc

        f = tmp_path / "r.pdf"
        f.write_bytes(b"x")

        assert _try_upload(page, ["input[type='file']"], f, "resume") is True
        loc.set_input_files.assert_called_once_with(str(f))

    def test_skips_when_count_is_zero(self, tmp_path: Path) -> None:
        page = MagicMock()
        loc = MagicMock()
        loc.count.return_value = 0
        page.locator.return_value.first = loc

        f = tmp_path / "r.pdf"
        f.write_bytes(b"x")

        assert _try_upload(page, ["input[type='file']"], f, "resume") is False
        loc.set_input_files.assert_not_called()


class TestFillForm:
    def test_greenhouse_splits_first_and_last(self, tmp_path: Path, mocker) -> None:
        page = MagicMock()
        try_fill = mocker.patch("jobpilot.apply._try_fill", return_value=True)

        _fill_form(page, _profile(), "greenhouse", None, None)

        called_fields = [call.args[3] for call in try_fill.call_args_list]
        assert "first_name" in called_fields
        assert "last_name" in called_fields
        # first/last name calls used the right values
        first_call = next(c for c in try_fill.call_args_list if c.args[3] == "first_name")
        last_call = next(c for c in try_fill.call_args_list if c.args[3] == "last_name")
        assert first_call.args[2] == "Yusheng"
        assert last_call.args[2] == "D."

    def test_lever_uses_full_name(self, mocker) -> None:
        page = MagicMock()
        try_fill = mocker.patch("jobpilot.apply._try_fill", return_value=True)

        _fill_form(page, _profile(), "lever", None, None)

        called_fields = [call.args[3] for call in try_fill.call_args_list]
        assert "full_name" in called_fields
        assert "first_name" not in called_fields
        full_call = next(c for c in try_fill.call_args_list if c.args[3] == "full_name")
        assert full_call.args[2] == "Yusheng D."

    def test_skips_email_when_profile_email_is_none(self, mocker) -> None:
        page = MagicMock()
        try_fill = mocker.patch("jobpilot.apply._try_fill", return_value=True)

        _fill_form(page, _profile(email=None), "greenhouse", None, None)

        called_fields = [call.args[3] for call in try_fill.call_args_list]
        assert "email" not in called_fields

    def test_unknown_ats_does_nothing(self, mocker) -> None:
        page = MagicMock()
        try_fill = mocker.patch("jobpilot.apply._try_fill", return_value=True)
        try_upload = mocker.patch("jobpilot.apply._try_upload", return_value=True)

        _fill_form(page, _profile(), "unknown", None, None)

        try_fill.assert_not_called()
        try_upload.assert_not_called()

    def test_uploads_resume_when_path_exists(self, tmp_path: Path, mocker) -> None:
        page = MagicMock()
        mocker.patch("jobpilot.apply._try_fill", return_value=True)
        try_upload = mocker.patch("jobpilot.apply._try_upload", return_value=True)
        resume_pdf = tmp_path / "r.pdf"
        resume_pdf.write_bytes(b"%PDF-1.4")

        _fill_form(page, _profile(), "greenhouse", resume_pdf, None)

        try_upload.assert_called_once()
        assert try_upload.call_args.args[2] == resume_pdf

    def test_skips_resume_upload_when_file_missing(self, tmp_path: Path, mocker) -> None:
        page = MagicMock()
        mocker.patch("jobpilot.apply._try_fill", return_value=True)
        try_upload = mocker.patch("jobpilot.apply._try_upload", return_value=True)
        missing = tmp_path / "nope.pdf"  # not created

        _fill_form(page, _profile(), "greenhouse", missing, None)

        try_upload.assert_not_called()

    def test_fills_cover_letter_textarea_for_greenhouse(self, tmp_path: Path, mocker) -> None:
        page = MagicMock()
        try_fill = mocker.patch("jobpilot.apply._try_fill", return_value=True)
        cover = tmp_path / "cover.md"
        cover.write_text("Hello, this is my cover letter for the role.")

        _fill_form(page, _profile(), "greenhouse", None, cover)

        cover_calls = [c for c in try_fill.call_args_list if c.args[3] == "cover_letter"]
        assert len(cover_calls) == 1
        assert "cover letter" in cover_calls[0].args[2]


class TestApplicator:
    def test_raises_clear_error_when_playwright_missing(self, monkeypatch) -> None:
        # Simulate playwright not installed
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "playwright.sync_api":
                raise ImportError("No module named 'playwright'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(RuntimeError, match="Playwright not installed"):
            Applicator()

    def test_apply_to_dispatches_to_known_ats(self, mocker) -> None:
        # Build an Applicator with all playwright internals mocked
        appl = Applicator.__new__(Applicator)
        appl.headless = True
        appl.slow_mo_ms = 0
        appl._context = MagicMock()
        appl._browser = MagicMock()
        appl._pw = MagicMock()

        page = MagicMock()
        appl._context.new_page.return_value = page

        fill_form = mocker.patch("jobpilot.apply._fill_form")

        ats = appl.apply_to("https://boards.greenhouse.io/anthropic/jobs/1", _profile(), None, None)

        assert ats == "greenhouse"
        page.goto.assert_called_once()
        fill_form.assert_called_once()
        assert fill_form.call_args.args[2] == "greenhouse"

    def test_apply_to_unknown_ats_skips_fill(self, mocker) -> None:
        appl = Applicator.__new__(Applicator)
        appl._context = MagicMock()
        page = MagicMock()
        appl._context.new_page.return_value = page

        fill_form = mocker.patch("jobpilot.apply._fill_form")

        ats = appl.apply_to("https://example.com/careers/foo", _profile(), None, None)

        assert ats == "unknown"
        fill_form.assert_not_called()

    def test_apply_to_requires_context_manager(self) -> None:
        appl = Applicator.__new__(Applicator)
        appl._context = None  # never entered as context manager

        with pytest.raises(RuntimeError, match="context manager"):
            appl.apply_to("https://x/y", _profile(), None, None)


class TestAtsConfigsHaveExpectedFields:
    """Sanity check that each ATS config covers the fields _fill_form expects."""

    def test_greenhouse_has_all_fields(self) -> None:
        cfg = ATS_CONFIGS["greenhouse"]
        for field in (
            "first_name",
            "last_name",
            "email",
            "phone",
            "resume_upload",
            "cover_letter_textarea",
        ):
            assert field in cfg, f"greenhouse missing {field}"
            assert len(cfg[field]) >= 1

    def test_lever_has_full_name_and_resume(self) -> None:
        cfg = ATS_CONFIGS["lever"]
        assert "full_name" in cfg
        assert "resume_upload" in cfg

    def test_ashby_has_full_name_and_resume(self) -> None:
        cfg = ATS_CONFIGS["ashby"]
        assert "full_name" in cfg
        assert "resume_upload" in cfg
