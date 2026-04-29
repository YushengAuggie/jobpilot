# Setting up Notion

jobpilot writes the daily shortlist into a Notion database you own. Here's how to wire it up.

## 1. Create a Notion integration

1. Go to https://www.notion.so/my-integrations
2. Click **+ New integration**
3. Name it (e.g. `jobpilot`), associate with your workspace, give it **read + write** content capabilities
4. Submit, then copy the **Internal Integration Secret** — this is your `NOTION_TOKEN`

Add it to `.env`:

```
NOTION_TOKEN=secret_...
```

## 2. Create a parent page

The integration can only read/write pages and databases that have been explicitly **shared** with it.

1. In Notion, create a new blank page (e.g. "Job Hunt") or pick an existing one
2. Click the `•••` menu in the top right → **Connections** → add your `jobpilot` integration
3. Copy the page ID from the URL — it's the 32-char hex string at the end:

   ```
   https://www.notion.so/Job-Hunt-1234567890abcdef1234567890abcdef
                                  ^ this part (with or without dashes)
   ```

## 3. Initialize the database

Run:

```bash
uv run python -m jobpilot init-notion --parent-page-id 1234567890abcdef1234567890abcdef
```

This creates a database under your page with the schema jobpilot expects (Title, Company, Score, Why match, Salary, Stage, URL, Status, Source, Found). It prints the new database ID.

Add it to `.env`:

```
NOTION_DB_ID=...
```

You're done. `uv run python -m jobpilot run-daily --dry-run` should now run cleanly.

## How jobpilot uses the database

| Field | Role |
|---|---|
| `URL` | **Dedup key.** The daily run pulls all existing URLs at the start and skips any postings already there. |
| `Status` | **Lifecycle state.** New (just discovered) → Approved (you marked for application) → Materials-Ready (v1.1 generated tailored resume) → Submitted (you sent it) → Rejected/Skip (no longer of interest). |
| `Score` | Claude's 0–10 match score. |
| `Why match` | Bullet-point reasons from Claude. |

You drive triage entirely from Notion: filter the view by `Status = New`, sort by `Score`, flip the rows you like to `Approved`. The local v1.2 skill picks up Approved rows for tailoring + browser pre-fill.

## Multiple databases / multiple users

If you want separate databases per role search ("AI roles" vs "infra roles"), repeat the init step with a different parent page and use a different `NOTION_DB_ID` per profile.
