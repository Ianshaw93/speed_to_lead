# Railway Deployment Skill

Deploy changes to Railway with full verification workflow.

## Instructions

Follow this exact workflow:

### 1. Run Tests
- Run the full test suite with `pytest`
- If any tests fail, stop and report the failures
- Do NOT proceed to deployment if tests fail

### 2. Commit and Push
- Stage all relevant changes
- Create a descriptive commit message
- Push to GitHub (this triggers Railway deployment)

### 3. Monitor Build
- Wait approximately 90 seconds for the build to complete
- Use `railway logs --follow` to monitor the deployment
- Watch for any build or startup errors

### 4. Verify Deployment
- Check Railway logs for application errors using `railway logs`
- Hit the `/health` endpoint to verify the service is running:
  ```
  curl https://<railway-url>/health
  ```
- Report the health check response

### 5. Report Status
- Summarize the deployment outcome
- If any step failed, provide details on what went wrong
