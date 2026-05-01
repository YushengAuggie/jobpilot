"""Load and validate profile.yaml + .env."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

from jobpilot.models import Profile

# Matches ${VAR} or ${VAR:-default}. Default extends to the closing brace.
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


def _resolve_env_vars(value: object) -> object:
    """Replace ${VAR} placeholders with values from os.environ. Recurses into dicts/lists.

    Bash-compatible default syntax: ${VAR:-default} resolves to `default` when the
    env var is unset, instead of raising. ${VAR} (no default) still raises so real
    config errors aren't silently swallowed.
    """
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            var = match.group(1)
            default = match.group(2)
            resolved = os.environ.get(var)
            if resolved is None:
                if default is not None:
                    return default
                raise ValueError(
                    f"profile.yaml references ${{{var}}} but it is not set in the environment. "
                    f"Either set the env var, or use ${{{var}:-default}} to provide a fallback."
                )
            return resolved

        return ENV_VAR_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def load_profile(path: Path | str = "profile.yaml", env_path: Path | str = ".env") -> Profile:
    """Load profile.yaml, resolving env-var placeholders against .env + os.environ."""
    env_file = Path(env_path)
    if env_file.exists():
        load_dotenv(env_file)

    profile_file = Path(path)
    if not profile_file.exists():
        raise FileNotFoundError(
            f"{profile_file} not found. Copy profile.example.yaml to {profile_file} and edit."
        )

    raw = yaml.safe_load(profile_file.read_text())
    resolved = _resolve_env_vars(raw)
    return Profile.model_validate(resolved)


def require_env(name: str) -> str:
    """Fetch a required env var, raising a clear error if missing."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required env var {name} is not set. Add it to .env.")
    return value
