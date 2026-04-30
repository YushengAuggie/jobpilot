# Contributing to jobpilot

Thanks for your interest. The codebase is small and intended to stay that way — keep changes focused and well-tested.

## Dev setup

```bash
git clone https://github.com/<you>/jobpilot && cd jobpilot
uv sync --extra dev
uv run pytest          # 60+ tests, no API keys required
uv run ruff check src tests
```

## Test layers

The test suite has three layers, gated by pytest markers:

| Marker | Scope | External deps | Required keys | When it runs |
|---|---|---|---|---|
| `unit` | Pure logic — schema validation, filters, dedup, prompt construction | None | None | Every PR + every commit |
| `replay` | Source clients, scoring, Notion sink — using mocked HTTP / mock clients | Recorded fixtures | None | Every PR + every commit |
| `live` | Real APIs (Anthropic, Notion sandbox, source endpoints) | Live network | All | Nightly only |

`pytest` defaults to `unit + replay` so you can fork, `uv sync`, and run a green build with zero API keys.

To run live tests locally:

```bash
ANTHROPIC_API_KEY=... NOTION_TOKEN=... NOTION_DB_ID=<sandbox-db> uv run pytest -m live
```

## Adding a new source

The most common contribution. See `docs/adding-a-source.md` for a step-by-step walkthrough.

In short:
1. Implement `src/jobpilot/sources/<name>.py` matching the `Source` protocol in `sources/base.py`
2. Call `register(<YourSource>())` at module bottom
3. Import in `sources/__init__.py`
4. Add `tests/test_<name>.py` with replay tests using `respx`
5. Update `profile.example.yaml` if your source needs configuration

## Code style

- Python 3.12+
- `ruff` for lint + format. Run before pushing.
- Type hints encouraged; strict mypy is not yet wired into CI but `mypy src/jobpilot` should pass.
- Default to no comments; only add them when WHY is non-obvious.
- Don't add error handling, fallbacks, or validation for scenarios that can't happen — trust internal code.

## Commits + PRs

- One logical change per PR
- Update tests for any behavioral change
- For breaking changes (profile schema, Notion DB schema, CLI flags), call it out in the PR description with a migration note

## Personal data safety

jobpilot has three layers of protection so personal data (your `profile.yaml`, resume, API keys) never reaches the public repo:

1. **`.gitignore`** — blocks the obvious files.
2. **Pre-commit hook** (`scripts/pre-commit`) — refuses the commit if any blocked path is staged, even with `git add -f`. Install once after cloning: `./scripts/install-hooks.sh`.
3. **CI workflow** (`.github/workflows/secrets-check.yml`) — re-runs the same path check against PR diffs and runs `gitleaks` for API-key/token detection. Fails the build before merge.

If you find a gap in these layers, please open an issue tagged `security`.

## Reporting issues

When filing a bug, include:
- What you ran (command or workflow file)
- Expected vs actual behavior
- Relevant log lines (with `-v` for verbose)
- Your `profile.yaml` schema **without personal values** (just field names + types)
