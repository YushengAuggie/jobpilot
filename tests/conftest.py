"""Shared pytest fixtures and configuration."""

from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def cassettes_dir() -> Path:
    return Path(__file__).parent / "cassettes"
