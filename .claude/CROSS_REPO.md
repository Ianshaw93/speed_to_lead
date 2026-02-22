# Smiths Cross-Repo Knowledge

This file is the shared knowledge base for all 3 smiths projects. It lives at the parent `smiths/` directory and is referenced by each project's CLAUDE.md.

**When working in any smiths project, read this file for cross-cutting context.**

## System Overview

| Project | Path | Purpose | Deployed? |
|---------|------|---------|-----------|
| **speed_to_lead** | `speed_to_lead/` | FastAPI backend, prospect tracking, sales funnel, database models | Yes (Railway) |
| **multichannel-outreach** | `multichannel-outreach/` | Messaging pipelines, outreach automation, webhook orchestration | Scripts only (Modal webhooks) |
| **contentCreator** | `contentCreator/` | LinkedIn content generation, drafts, hooks, ideas | Scripts only |

## Shared Infrastructure

### Database
- **Single PostgreSQL instance** on Railway, shared by all 3 projects
- Internal URL: `postgres.railway.internal` (only accessible from Railway)
- Public proxy: `crossover.proxy.rlwy.net:56267` (for local queries)
- Connection string in `DATABASE_URL` env var
- **speed_to_lead** owns the schema (Alembic migrations)
- **contentCreator** has its own tables (Draft, Hook, Idea, Insight, Image) via SQLAlchemy `create_tables()`

### API Server
- **Single deployed API** at `https://speedtolead-production.up.railway.app`
- Source: `speed_to_lead` repo (Railway auto-deploys from GitHub pushes)
- All API endpoints must be added to `speed_to_lead` — other repos call into it

## Sales Funnel

```
Connection Req Sent → Accepted → Initial Msg → Positive Reply → Pitched → Calendar Shown → Booked
```

Key fields on `Prospect` model: `connection_sent_at`, `connection_accepted_at`, `positive_reply_at`, `pitched_at`, `calendar_sent_at`, `booked_at`

Enum: `FunnelStage` (POSITIVE_REPLY, PITCHED, CALENDAR_SENT, BOOKED)

## Available API Endpoints (speed_to_lead)

### Admin/Metrics
- `GET /admin/prospects/funnel` - Prospects at pitched+ stage
- `GET /admin/draft/{draft_id}` - View draft content
- `POST /admin/send-draft/{draft_id}` - Send draft via HeyReach
- `POST /admin/run-migrations` - Run DB migrations (needs `Authorization: Bearer SECRET_KEY`)
- `GET /health` - Health check

### Webhooks
- `POST /webhook/heyreach` - HeyReach event ingestion
- `POST /webhook/buying-signal` - Buying signal from Gojiberry

## Cross-Repo Data Flows

1. **multichannel-outreach** discovers prospects → creates them in shared DB → **speed_to_lead** tracks funnel
2. **speed_to_lead** identifies positive replies → triggers pitched message flow
3. **contentCreator** generates content → drafts stored in DB → can be used for outreach messaging
4. **Buying signals** (from Gojiberry webhook) → stored on prospects → influence message personalization in **multichannel-outreach**

## Cost Tracking

**All actions that incur a cost (API calls, Apify runs, LLM tokens, etc.) MUST be logged to the speed_to_lead database.**

- **Existing model**: `PipelineRun` in `speed_to_lead/app/models.py` tracks prospecting pipeline costs
- **For non-pipeline costs** (e.g. contentCreator AI generation, multichannel-outreach Apify scraping): log to a generic `CostLog` table (to be created in speed_to_lead when first needed)
- **Required fields**: source repo, action name, service (apify/openai/anthropic/perplexity/etc.), cost amount, timestamp
- **Why**: Visibility into total spend across the system. No action that costs money should be invisible.

This applies to all 3 repos. When writing or modifying scripts that call paid APIs, ensure cost is captured and logged.

## Health Check System

Production health checks run 2x/day (10am, 3pm UK) in the speed_to_lead service, querying the DB for liveness signals.

- **Code**: `speed_to_lead/app/services/health_check.py`
- **Directive**: `multichannel-outreach/directives/health_check_system.md`
- **Endpoints**: `POST /admin/health-check` (auth), `GET /admin/health-check/status` (no auth)
- **Adding a check**: Add an `async def check_<name>(session) -> CheckResult` function + add to `ALL_CHECKS` list + write tests

**After completing work that introduces a new data flow or integration, assess whether a health check should be added.** If the feature has data that should appear regularly when working (liveness signal), add a check. Examples: new webhook, scheduled task, external service, pipeline writing to DB.

## Shared Conventions

- **Railway CLI in Git Bash**: Always use `cmd.exe /c "railway ..."` wrapper
- **Database migrations**: Only via speed_to_lead's Alembic
- **PostgreSQL enums**: Use `DO $$ BEGIN CREATE TYPE ... EXCEPTION WHEN duplicate_object THEN NULL; END $$` for idempotent creation
- **Environment**: All projects use `.env` files, Railway env vars in production

## How to Update This File

When working in any smiths project and you create something cross-cutting (new endpoint, shared convention, data flow change), update this file. Other Claude instances in sibling repos will read it for context.
