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

## Test Commands

```bash
pytest -v                                    # Run all tests
pytest tests/test_file.py::test_name -v     # Run specific test
pytest --live                                # Include live API tests
```

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
cmd.exe /c "railway deployment list"              # List deployments
cmd.exe /c "railway logs --build --lines 100 ID"  # Build logs
cmd.exe /c "railway logs --lines 50 --since 1h"   # Runtime logs
cmd.exe /c "railway status"                       # Check link status
cmd.exe /c "railway redeploy --yes"               # Trigger redeploy
```

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
