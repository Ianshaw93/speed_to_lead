# Speed to Lead

## Related Projects

This is part of a 3-project prospecting/outreach system:

| Project | Path | Purpose |
|---------|------|---------|
| **speed_to_lead** | `C:\Users\IanShaw\localProgramming\smiths\speed_to_lead` | Prospecting & lead tracking |
| **multichannel-outreach** | `C:\Users\IanShaw\localProgramming\smiths\multichannel-outreach` | Messaging & outreach automation |
| **contentCreator** | `C:\Users\IanShaw\localProgramming\smiths\contentCreator` | Content generation |

## Project Structure

- FastAPI app in `app/`
- SQLAlchemy models in `app/models.py`
- Pydantic schemas in `app/schemas.py`
- Services in `app/services/`
- Prompts in `app/prompts/`
- Tests in `tests/` (mirrors source structure)
- Alembic migrations in `alembic/versions/`

## Sales Funnel Stages

The funnel progresses linearly:

```
Connection Req Sent → Connection Accepted → Initial Msg Sent → Positive Reply → Pitched → Calendar Shown → Booked
```

- **Connection Req Sent** (`connection_sent_at`) - Outreach initiated via HeyReach
- **Connection Accepted** (`connection_accepted_at`) - Prospect accepted connection
- **Initial Msg Sent** - First message delivered (tracked in HeyReach)
- **Positive Reply** (`positive_reply_at` / `FunnelStage.POSITIVE_REPLY`) - Lead replied positively
- **Pitched** (`pitched_at` / `FunnelStage.PITCHED`) - We invited them to schedule a call
- **Calendar Shown** (`calendar_sent_at` / `FunnelStage.CALENDAR_SENT`) - They agreed, we sent calendar link
- **Booked** (`booked_at` / `FunnelStage.BOOKED`) - They booked into calendar

Tracked on both `Prospect` model (timestamp fields) and `Conversation` model (`funnel_stage` enum).

## Database

**IMPORTANT**: This project uses the Railway-hosted PostgreSQL database, not a local database.

- Database is hosted on Railway (internal URL: `postgres.railway.internal`)
- Migrations must be run from within Railway's environment, not locally
- To run migrations: `cmd.exe /c "railway shell"` then `alembic upgrade head`
- Or trigger via redeploy if migrations are set to run on startup

## Test Commands

```bash
pytest -v                                    # Run all tests
pytest tests/test_file.py::test_name -v     # Run specific test
pytest --live                                # Include live API tests
```

**WARNING: `pytest -v` (full suite) can hang indefinitely.** Always run specific test files instead of the full suite. If you must run all tests, use a timeout: `timeout 120 pytest -v` and be prepared to kill it.

## Deployment

This project is deployed on Railway. Deployment is triggered by pushing to GitHub.

### Workflow

1. Run tests locally: `pytest -v`
2. Push to GitHub (triggers auto-deploy)
3. Monitor: `cmd.exe /c "railway deployment list"`
4. Verify: `curl https://speedtolead-production.up.railway.app/health`

### Railway CLI (Git Bash Compatibility)

Railway CLI requires `cmd.exe /c` wrapper in Git Bash:

```bash
cmd.exe /c "railway status"                       # ALWAYS CHECK THIS FIRST - shows which service CLI is linked to
cmd.exe /c "railway deployment list"              # List deployments
cmd.exe /c "railway logs --build --lines 100 ID"  # Build logs
cmd.exe /c "railway logs --lines 50 --since 1h"   # Runtime logs
cmd.exe /c "railway redeploy --yes"               # Trigger redeploy
```

**CRITICAL**: Before running ANY Railway CLI command that modifies state (redeploy, etc.), ALWAYS run `railway status` first to confirm you're linked to the correct service (app, not Postgres). The CLI may be linked to the Postgres service, and `railway redeploy` on Postgres restarts the database instead of the app.

### Railway MCP Server

Use Railway MCP server for:
- `get-logs` - Retrieve service logs (preferred)
- `list-projects` / `list-services` / `list-variables`
- `deploy` - Deploy changes

### Troubleshooting

1. Get deployment ID: `cmd.exe /c "railway deployment list"`
2. Check build logs: `cmd.exe /c "railway logs --build --lines 100 <id>"`
3. Check runtime logs: `cmd.exe /c "railway logs --lines 50 --since 10m"`
4. If CLI fails, use Railway MCP server or dashboard

### Project IDs

- **URL**: https://speedtolead-production.up.railway.app
- Project: `2956fc54-0e6e-4f35-b6c3-2efe2240d602`
- Environment: `d8e53f68-c745-4e25-bde6-10a46ac93495`
- Service: `7cab4889-3675-4ef5-870c-63e803ce7082`
