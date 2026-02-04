# Railway Deployment Skill

Deploy changes to Railway with verification and monitoring.

## Instructions

### 1. Pre-Deploy Verification (CRITICAL)

First, run tests to verify changes are safe to deploy:
```bash
pytest -v
```

**If any tests fail: STOP immediately and report failures. Do NOT proceed.**

### 2. Review Changes
```bash
git status
git diff --stat
```

Summarize what will be deployed.

### 3. Commit and Push

Stage specific files (avoid `git add -A`):
```bash
git add <specific files>
git commit -m "$(cat <<'EOF'
<descriptive message>

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
git push
```

### 4. Monitor Build (Start Immediately)

Don't wait - start monitoring right after push:
```bash
sleep 5 && cmd.exe /c "railway deployment list"
```

Get deployment ID and check build logs:
```bash
cmd.exe /c "railway logs --build --lines 50 <deployment_id>"
```

Poll every 10-15 seconds until SUCCESS or FAILED.

### 5. Health Check

Once deployment shows SUCCESS:
```bash
curl -s https://speedtolead-production.up.railway.app/health
```

### 6. Report Status

```
## Deployment Summary
- Tests: X passed
- Commit: <hash>
- Deployment: <id>
- Status: SUCCESS / FAILED
- Health: healthy / unhealthy
```

## On Failure

- **Test failure**: List failing tests, suggest fixes
- **Build failure**: Show build log errors
- **Health failure**: Check runtime logs with `cmd.exe /c "railway logs --lines 50 --since 5m"`

## Alternative: Subagent Workflow

For more thorough verification, use the chained workflow:
```
Use the checker subagent to verify my changes, then use the deployer subagent to deploy
```
