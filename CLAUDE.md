# Claude Code Instructions

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
- Wait ~30 seconds, then check build logs using `railway logs`
- Wait ~90 seconds total for deployment to complete
- Watch for any build or startup errors in the logs

#### 5. Verify Deployment
- Check Railway project logs for application errors
- Hit the `/health` endpoint to verify the service is running
- Test any new endpoints or functionality in production

### Railway CLI Commands

- `railway logs` - View recent deployment logs
- `railway logs --follow` - Stream logs in real-time
- `railway status` - Check deployment status
- `railway variables` - List environment variables
- `railway run <command>` - Run a command with Railway environment variables

### Health Check

After deployment, always verify the service is healthy:
```
curl https://<railway-url>/health
```

### Troubleshooting Deployment Failures

**IMPORTANT**: When deployments fail, always check the **deploy logs** (not just build logs):

1. **Build logs** show if the Docker image was built successfully
2. **Deploy logs** show what happens when the container starts - this is where startup crashes appear

Common issues to look for in deploy logs:
- Import errors (missing dependencies)
- Environment variable issues (e.g., `${PORT:-8000}` not interpreted - needs `sh -c` wrapper)
- Database connection failures
- Missing required config

If the Railway CLI isn't working or isn't linked to the project:
1. Ask the user to run `railway login` and `railway link` to connect the CLI
2. Fallback: Ask the user to check the Railway dashboard for deploy logs

**Railway URL**: https://speedtolead-production.up.railway.app
