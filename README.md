# jobpilot

**Your daily AI-curated job shortlist + tailored applications. Open-source.**

jobpilot fetches job postings from public sources, scores each one against your profile with Claude, drops a ranked daily shortlist into a Notion database, and (when you're ready) tailors your resume + cover letter and pre-fills the application in your browser. You click submit.

It does the curation work and leaves submission to you. No spammy auto-apply, no account-ban risk, no ToS roulette.

## What it does

- **Source** — fetches postings daily from Hacker News "Who is Hiring", Greenhouse, Lever, and Ashby boards (configurable per company)
- **Score** — Claude scores each posting 0–10 against your profile (target roles, strengths, salary floor, stage, dealbreakers) with prompt caching so per-posting cost stays low
- **Triage** — top picks land in your Notion database with a Status field. You flip rows to `Approved` to mark for application
- **Apply** *(v1.2, coming soon)* — local Claude Code skill tailors your resume + cover letter and opens the application form pre-filled. You review and submit

## Why it exists

Manual job hunting at scale is brutal. Existing tools either spam-apply (which doesn't work and burns reputation) or are passive job boards. jobpilot does the hard part — sifting hundreds of postings down to the few worth your attention — and leaves the human-judgement parts (final review, cover letter polish, "submit") to you.

## Quickstart

The first 5 minutes have no API keys involved — you can verify sources work before paying for anything.

### 1. Install

```bash
git clone https://github.com/<you>/jobpilot && cd jobpilot

# Pick one. uv is faster; pip works too.
uv sync                                        # https://docs.astral.sh/uv/getting-started/installation/
# OR
pip install -e .
```

### 2. Smoke-test sources (no keys yet)

```bash
cp profile.example.yaml profile.yaml          # edit: target_roles, salary, ats_boards
uv run python -m jobpilot run-daily --no-score --limit 3 --source hn
```

You should see real Hacker News "Who's Hiring" postings printed to your terminal. If this works, sources are reachable. No API calls. No Notion writes. **If it fails here**, the README's setup steps don't apply — open an issue.

### 3. Add API keys

```bash
cp .env.example .env
# Edit .env and paste:
#   ANTHROPIC_API_KEY=sk-ant-... (from https://console.anthropic.com/)
#   NOTION_TOKEN=secret_...      (from https://www.notion.so/my-integrations)
```

For the Notion integration, see [`docs/notion.md`](docs/notion.md) — three steps: create the integration, create a parent page in Notion, share that page with the integration via its `•••` menu → Connections.

### 4. Initialize the Notion database

```bash
uv run python -m jobpilot init-notion --parent-page-id <32-char-page-id-from-the-Notion-URL>
```

Copy the printed `NOTION_DB_ID` into `.env`.

### 5. Preview the full pipeline (real scoring, no Notion writes)

```bash
uv run python -m jobpilot run-daily --dry-run --limit 3
```

This costs a few cents in Anthropic API. You'll see the same postings as step 2, now with actual scores against your profile. Sanity-check that the top score makes sense for what you'd actually want.

### 6. Real run

```bash
uv run python -m jobpilot run-daily
```

Open the Notion page. You'll see a ranked shortlist.

## Configuration

`profile.yaml` is gitignored — it stays on your machine. Schema:

| Field | Type | Notes |
|---|---|---|
| `name` | string | Your name |
| `resume_path` | path | Absolute path to your base resume PDF/markdown |
| `target_roles` | list | Role titles you'd accept (used in scoring rubric) |
| `strengths` | list | Top skills (3–7 items) |
| `salary_min_usd` | int | Postings below this floor are dropped |
| `stages` | list | Allowed company stages: `seed`, `series-a`, `series-b`, `series-c`, `public` |
| `locations` | list | Locations you'd take (free-text, matched loosely) |
| `dealbreakers` | list | Keywords that auto-reject (case-insensitive substring match on JD) |
| `ats_boards` | object | Per-provider company slugs: `greenhouse`, `lever`, `ashby` |
| `notion.database_id` | string | `${NOTION_DB_ID}` — resolved from env |
| `score_threshold` | float | Postings below this score are dropped (default 6) |
| `daily_limit` | int | Max new rows added to Notion per run (default 25) |

## How it works

```
┌──────────────────────────── daily, cloud ────────────────────────────┐
│  Sources → Dedupe → Score (Claude) → Filter → Notion DB (Status=New) │
└──────────────────────────────────────────────────────────────────────┘
                                                          │
                                       you triage in Notion (Status=Approved)
                                                          │
┌──────────────────────────── on demand, local ───────────────────────┐
│  Notion poll → Tailor resume+cover (Claude) → Open browser pre-filled│
└──────────────────────────────────────────────────────────────────────┘
```

**Persistence**: Notion is the only persistent store. The `URL` field is the dedup key — each daily run pulls all existing URLs from your DB and skips them. `Status` tracks the lifecycle (New → Approved → Materials-Ready → Submitted → Rejected/Skip). No separate database needed.

**Caching**: Claude prompt caching keeps the scoring rubric + your profile cached across all postings in a run. Per-posting cost is roughly the JD length only.

## Running daily on GitHub Actions

A workflow at `.github/workflows/daily.yml` runs the pipeline every day at 15:00 UTC (8am PT). To enable:

1. **Get local `run-daily` working first.** Actions runs the same code with secrets injected; debugging a misconfigured Notion integration through Actions logs (with secrets masked) is brutal.
2. Push the repo to GitHub.
3. Add four secrets under Settings → Secrets → Actions:
   - `ANTHROPIC_API_KEY`
   - `NOTION_TOKEN`
   - `NOTION_DB_ID`
   - `PROFILE_YAML` — paste the **entire contents** of your `profile.yaml`. The CLI is more reliable than the web paste box for multi-line content:
     ```bash
     gh secret set PROFILE_YAML < profile.yaml
     ```
4. Trigger once manually: `gh workflow run daily.yml`.

Since `profile.yaml` is gitignored, the workflow reconstructs it from the `PROFILE_YAML` secret at runtime. Your repo can be public; nothing personal is committed.

## Adding a new source

Sources implement a tiny protocol. To add one:

1. Create `src/jobpilot/sources/<name>.py`
2. Implement a class with `name: str` and `list_jobs(profile: Profile, limit: int) -> list[JobPosting]`
3. Call `register(<YourSource>())` at the bottom of the module
4. Import it in `src/jobpilot/sources/__init__.py` so the registration runs
5. Add a replay test under `tests/test_<name>.py` using `respx`

See `docs/adding-a-source.md` for a full walkthrough using `hackernews.py` as the reference.

## Cost

A typical daily run with ~300 postings costs about **$0.50–$2** in Claude API usage with Sonnet 4.6 (default scoring model). Notion is free at this scale. GitHub Actions is free for public repos.

To cut costs further, override `SCORING_MODEL` in `src/jobpilot/score.py` to `claude-haiku-4-5` — but note its cache prefix threshold (4096 tokens) is higher, so caching may miss for shorter rubrics.

## Roadmap

- [x] **v1.0** — Sources (HN + Greenhouse/Lever/Ashby) + Claude scoring + Notion daily digest + GitHub Actions
- [x] **v1.1** — `jobpilot tailor`: generate tailored resume + cover letter for Approved rows
- [x] **v1.2** — `jobpilot apply-pending`: open Materials-Ready URLs in Chromium with form pre-filled
- [ ] **v2** — LinkedIn source via authenticated browser

## Applying with auto-fill (v1.2)

Once `jobpilot tailor` produces materials and you flip the row to `Materials-Ready`:

```bash
uv sync --extra apply                  # one-time: install Playwright
uv run playwright install chromium     # one-time: download browser (~300MB)

# Recommended for resume upload (without it, you'll have to upload manually):
brew install pandoc                    # macOS
# apt install pandoc                   # Debian/Ubuntu
# https://pandoc.org/installing.html   # everywhere else

uv run python -m jobpilot apply-pending
```

For each row, jobpilot opens the application URL in a visible Chromium window, detects the ATS (Greenhouse / Lever / Ashby), and fills name, email, phone, resume upload, and cover letter where it can. You review what got filled, fix anything that didn't, and click submit. The terminal then asks "Submitted? y/N/skip" and updates Notion accordingly.

Auto-fill is best-effort — selectors drift. If a field doesn't fill, the page is still open; just type it in. Unknown ATS providers (LinkedIn, Workday, etc.) load without auto-fill so you can apply manually.

**Privacy note:** Tailoring sends your base resume + the JD to Anthropic. Scoring sends only the JD. If your resume contains information you don't want sent, redact it from the file `profile.yaml`'s `resume_path` points at, or skip `tailor` and write cover letters manually.

## Contributing

Issues and PRs welcome. See `CONTRIBUTING.md` for dev setup and the testing structure.

## License

MIT — see `LICENSE`.
