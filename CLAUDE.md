# Claude Code Instructions

## Related Projects

This is part of a 3-project prospecting/outreach system:

| Project | Path | Purpose |
|---------|------|---------|
| **speed_to_lead** | `C:\Users\IanShaw\localProgramming\smiths\speed_to_lead` | Prospecting & lead tracking |
| **multichannel-outreach** | `C:\Users\IanShaw\localProgramming\smiths\multichannel-outreach` | Messaging & outreach automation |
| **contentCreator** | `C:\Users\IanShaw\localProgramming\smiths\contentCreator` | Content generation |

## Test-Driven Development (TDD)

When implementing new features or fixing bugs, follow this TDD workflow:

### 1. Write Tests First
- Before writing any implementation code, write failing tests that define the expected behavior
- Tests should be clear, focused, and test one thing at a time
- Include edge cases and error conditions

### 2. Run Tests (Verify They Fail)
- Execute the test suite to confirm the new tests fail
- This validates that the tests are actually testing something meaningful

### 3. Write Implementation Code
- Write the minimum code necessary to make the tests pass
- Focus on making tests pass, not on perfect code

### 4. Run Tests (Verify They Pass)
- Execute the full test suite to ensure all tests pass
- If any tests fail, fix the implementation until all tests pass

### 5. Refactor (Optional)
- Once tests pass, refactor code for clarity and maintainability
- Re-run tests after refactoring to ensure nothing broke

## Key Principles

- Never consider a feature complete until tests pass
- Tests are documentation - write them to be readable
- When fixing bugs, write a test that reproduces the bug first

## Deployment Workflow

This project is deployed on Railway. Deployment is triggered by pushing to GitHub.

### Complete Feature/Fix Workflow

Follow this end-to-end workflow for all changes:

#### 1. Write Tests First (TDD)
- Write failing tests that define expected behavior
- Run tests to verify they fail

#### 2. Write Implementation Code
- Write code to make tests pass
- Run tests to verify they pass

#### 3. Push to GitHub
- Once all tests pass, commit and push changes to GitHub
- This triggers automatic deployment to Railway

#### 4. Monitor Deployment
**IMPORTANT: Monitor immediately as it builds - don't wait 60+ seconds!**
- After pushing, wait 5-10 seconds then start checking
- Get deployment status: `cmd.exe /c "railway deployment list"` (check every 10-15 seconds)
- Check build/deploy logs as soon as DEPLOYING: `cmd.exe /c "railway logs --deployment <deployment_id>"`
- Watch for build errors and healthcheck results in real-time

#### 5. Verify Deployment
- Confirm deployment status is SUCCESS: `cmd.exe /c "railway deployment list"`
- Hit the `/health` endpoint: `curl https://speedtolead-production.up.railway.app/health`
- Use Railway MCP server's `get-logs` for runtime logs if needed
- Test any new endpoints or functionality in production

### Railway MCP Server

This project has the Railway MCP server configured. Use it for:
- `get-logs` - Retrieve service logs (preferred method)
- `list-projects` - List Railway projects
- `list-services` - List services in a project
- `list-variables` - Get environment variables
- `deploy` - Deploy changes

### Railway CLI Commands

**IMPORTANT: Git Bash Compatibility**

Railway CLI does not output properly in Git Bash. Always use `cmd.exe /c` to run Railway commands:
```bash
cmd.exe /c "railway deployment list"
cmd.exe /c "railway logs --build --lines 100 <deployment_id>"
```

**Working Commands:**
- `cmd.exe /c "railway status"` - Check current project/service link
- `cmd.exe /c "railway deployment list"` - List recent deployments with IDs and status
- `cmd.exe /c "railway logs --build --lines N <deployment_id>"` - View build logs
- `cmd.exe /c "railway logs --build --json --lines N <deployment_id>"` - Build logs in JSON format
- `cmd.exe /c "railway variables"` - List environment variables
- `cmd.exe /c "railway redeploy --yes"` - Trigger a redeploy
- `cmd.exe /c "railway variable set KEY=VALUE"` - Set environment variable

**Log Commands (Correct Syntax):**
```bash
# Build logs (these work reliably)
cmd.exe /c "railway logs --build --lines 100 <deployment_id>"

# Get deployment ID first
cmd.exe /c "railway deployment list"

# Runtime logs (may be empty if app doesn't log to stdout)
cmd.exe /c "railway logs --lines 50 --since 1h"
```

**Note:** The `-f` flag is for `--filter`, NOT for follow/streaming. Streaming is the default behavior without `--lines`/`--since`/`--until`.

### Health Check

After deployment, always verify the service is healthy:
```
curl https://<railway-url>/health
```

### Troubleshooting Deployment Failures

**Step 1: Get deployment ID and status**
```bash
cmd.exe /c "railway deployment list"
```

**Step 2: Check build logs** (these reliably work)
```bash
cmd.exe /c "railway logs --build --lines 100 <deployment_id>"
```

**Step 3: Check runtime logs** (use MCP server if CLI returns empty)
```bash
# Try CLI first
cmd.exe /c "railway logs --lines 50 --since 10m"

# If empty, use MCP server's get-logs tool
```

**Common Issues:**
- Build failures: Check `--build` logs for pip/docker errors
- Startup crashes: Look for import errors, missing env vars in build logs after "Healthcheck" section
- If healthcheck fails, the error appears in build logs
- Runtime logs may be empty if app doesn't flush stdout (PYTHONUNBUFFERED=1 is set)

**If Railway CLI isn't working:**
1. Make sure you're using `cmd.exe /c "railway ..."` (Git Bash doesn't show output otherwise)
2. Run `cmd.exe /c "railway login"` and `cmd.exe /c "railway link"` to connect
3. Use Railway MCP server tools as fallback
4. Check Railway dashboard for deploy logs

**Railway URL**: https://speedtolead-production.up.railway.app

**Project IDs** (for API/debugging):
- Project: `2956fc54-0e6e-4f35-b6c3-2efe2240d602`
- Environment: `d8e53f68-c745-4e25-bde6-10a46ac93495`
- Service: `7cab4889-3675-4ef5-870c-63e803ce7082`
