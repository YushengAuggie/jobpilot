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

You'll need: a Notion workspace, an Anthropic API key, and a few minutes.

```bash
git clone https://github.com/<you>/jobpilot && cd jobpilot
uv sync

# Configure
cp profile.example.yaml profile.yaml          # edit: target roles, salary, stages, ATS slugs
cp .env.example .env                           # add ANTHROPIC_API_KEY + NOTION_TOKEN

# Set up Notion
# 1. Create an integration at https://www.notion.so/my-integrations — copy the token to NOTION_TOKEN
# 2. Create a Notion page where the database will live; share it with your integration
# 3. Run:
uv run python -m jobpilot init-notion --parent-page-id <your-page-id>
# Add the printed NOTION_DB_ID to .env

# Preview (no Notion writes)
uv run python -m jobpilot run-daily --dry-run --limit 5

# Real run
uv run python -m jobpilot run-daily
```

Open Notion. You'll see a ranked shortlist.

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

1. Push the repo to GitHub
2. Add four secrets under Settings → Secrets → Actions:
   - `ANTHROPIC_API_KEY`
   - `NOTION_TOKEN`
   - `NOTION_DB_ID`
   - `PROFILE_YAML` — paste the **entire contents** of your `profile.yaml`
3. Trigger once manually: `gh workflow run daily.yml`

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
- [ ] **v1.1** — `jobpilot tailor` command: generate tailored resume + cover letter for Approved rows
- [ ] **v1.2** — `jobpilot apply-pending` command + Claude Code skill: open application URL pre-filled in your browser
- [ ] **v2** — LinkedIn source via authenticated browser (gstack `browse` skill)

## Contributing

Issues and PRs welcome. See `CONTRIBUTING.md` for dev setup and the testing structure.

## License

MIT — see `LICENSE`.
