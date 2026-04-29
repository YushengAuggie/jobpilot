"""Unit tests for config loading and profile validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jobpilot.config import _resolve_env_vars, load_profile
from jobpilot.models import Profile, Stage

pytestmark = pytest.mark.unit


def _valid_profile_dict() -> dict:
    return {
        "name": "Tester",
        "resume_path": "/tmp/r.pdf",
        "target_roles": ["Senior Engineer"],
        "strengths": ["python"],
        "salary_min_usd": 150000,
        "stages": ["seed", "series-a"],
        "locations": ["Remote-US"],
        "dealbreakers": [],
        "ats_boards": {"greenhouse": ["stripe"]},
        "notion": {"database_id": "abc123"},
        "score_threshold": 6,
        "daily_limit": 25,
    }


def test_minimal_profile_validates() -> None:
    p = Profile.model_validate(_valid_profile_dict())
    assert p.name == "Tester"
    assert p.stages == [Stage.SEED, Stage.SERIES_A]
    assert p.ats_boards.greenhouse == ["stripe"]
    assert p.notion.database_id == "abc123"


def test_invalid_stage_rejected() -> None:
    bad = _valid_profile_dict()
    bad["stages"] = ["bogus-stage"]
    with pytest.raises(ValueError):
        Profile.model_validate(bad)


def test_negative_salary_rejected() -> None:
    bad = _valid_profile_dict()
    bad["salary_min_usd"] = -1
    with pytest.raises(ValueError):
        Profile.model_validate(bad)


def test_score_threshold_out_of_range() -> None:
    bad = _valid_profile_dict()
    bad["score_threshold"] = 11
    with pytest.raises(ValueError):
        Profile.model_validate(bad)


def test_resolve_env_vars_in_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_VAR", "hello")
    assert _resolve_env_vars("prefix-${MY_VAR}-suffix") == "prefix-hello-suffix"


def test_resolve_env_vars_in_nested(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_ID", "db_abc")
    raw = {"notion": {"database_id": "${DB_ID}"}, "list": ["${DB_ID}"]}
    out = _resolve_env_vars(raw)
    assert out == {"notion": {"database_id": "db_abc"}, "list": ["db_abc"]}


def test_resolve_env_vars_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEFINITELY_UNSET", raising=False)
    with pytest.raises(ValueError, match="DEFINITELY_UNSET"):
        _resolve_env_vars("${DEFINITELY_UNSET}")


def test_load_profile_resolves_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_DB_ID", "live_db_id")
    profile_data = _valid_profile_dict()
    profile_data["notion"] = {"database_id": "${NOTION_DB_ID}"}
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile_data))

    p = load_profile(profile_path, env_path=tmp_path / ".env-missing")
    assert p.notion.database_id == "live_db_id"


def test_load_profile_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_profile(tmp_path / "nope.yaml", env_path=tmp_path / ".env")
