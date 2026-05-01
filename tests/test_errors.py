"""Unit tests for the CLI friendly-error decorator."""

from __future__ import annotations

import pytest
import typer

from jobpilot._errors import _hint_for, friendly_errors

pytestmark = pytest.mark.unit


class TestHintFor:
    def test_hint_for_unauthorized(self) -> None:
        hint = _hint_for(RuntimeError("notion 401 Unauthorized"))
        assert hint is not None
        assert "shared with the integration" in hint.lower()

    def test_hint_for_object_not_found(self) -> None:
        hint = _hint_for(RuntimeError("object_not_found"))
        assert hint is not None
        assert "init-notion" in hint.lower()

    def test_hint_for_schema_drift(self) -> None:
        hint = _hint_for(
            RuntimeError("Title is not a property that exists. Score is not a property that exists.")
        )
        assert hint is not None
        assert "schema" in hint.lower()

    def test_hint_for_missing_env(self) -> None:
        hint = _hint_for(RuntimeError("Required env var NOTION_TOKEN is not set. Add it to .env."))
        assert hint is not None
        assert ".env" in hint

    def test_hint_for_missing_profile(self) -> None:
        hint = _hint_for(FileNotFoundError("profile.yaml not found"))
        assert hint is not None
        assert "profile.example.yaml" in hint

    def test_hint_for_missing_resume(self) -> None:
        hint = _hint_for(FileNotFoundError("Resume not found at /tmp/missing.pdf"))
        assert hint is not None
        assert "resume_path" in hint

    def test_hint_for_unresolved_var(self) -> None:
        hint = _hint_for(
            ValueError("profile.yaml references ${NOTION_DB_ID} but it is not set in the environment")
        )
        assert hint is not None
        assert "${VAR:-default}" in hint

    def test_hint_for_anthropic_auth(self) -> None:
        hint = _hint_for(RuntimeError("authentication failed: invalid x-api-key"))
        assert hint is not None
        assert "ANTHROPIC_API_KEY" in hint

    def test_hint_for_unknown_error(self) -> None:
        hint = _hint_for(RuntimeError("something completely unrelated"))
        assert hint is None


class TestFriendlyErrors:
    def test_passes_through_normal_return(self) -> None:
        @friendly_errors()
        def ok() -> str:
            return "ran"

        assert ok() == "ran"

    def test_converts_runtime_error_to_typer_exit(self, capsys: pytest.CaptureFixture[str]) -> None:
        @friendly_errors()
        def boom() -> None:
            raise RuntimeError("Required env var NOTION_TOKEN is not set. Add it to .env.")

        with pytest.raises(typer.Exit) as exc:
            boom()

        assert exc.value.exit_code == 1
        captured = capsys.readouterr()
        # Stderr (Console with stderr=True) — error message + hint
        assert "RuntimeError" in captured.err
        assert "NOTION_TOKEN" in captured.err
        assert ".env" in captured.err

    def test_keyboard_interrupt_propagates(self) -> None:
        @friendly_errors()
        def interrupted() -> None:
            raise KeyboardInterrupt()

        with pytest.raises(KeyboardInterrupt):
            interrupted()

    def test_typer_exit_propagates(self) -> None:
        @friendly_errors()
        def exit_normally() -> None:
            raise typer.Exit(code=2)

        with pytest.raises(typer.Exit) as exc:
            exit_normally()
        assert exc.value.exit_code == 2

    def test_verbose_kwarg_reraises_original(self) -> None:
        @friendly_errors()
        def boom(verbose: bool = False) -> None:
            raise ValueError("specific traceback wanted")

        with pytest.raises(ValueError, match="specific traceback wanted"):
            boom(verbose=True)
