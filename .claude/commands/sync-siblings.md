# Cross-Repo Sync

You are syncing cross-cutting knowledge across the 3 smiths projects. This command can be triggered explicitly or should be run proactively when you've created something that sibling repos should know about.

## The 3 Projects

| Project | Path | Purpose |
|---------|------|---------|
| **speed_to_lead** | `C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\speed_to_lead` | Prospecting & lead tracking |
| **multichannel-outreach** | `C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\multichannel-outreach` | Messaging & outreach automation |
| **contentCreator** | `C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\contentCreator` | Content generation |

## Shared Files

- **Canonical source**: `C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\CROSS_REPO.md`
- **In-repo copies**: Each repo has `.claude/CROSS_REPO.md` (committed to git, for web app access)

## Workflow

### Step 1: Identify what changed
Look at recent work in the current repo. Identify anything cross-cutting:
- New API endpoints that other repos might call
- Database schema changes (new models, fields, enums)
- New metrics or reporting capabilities
- Shared conventions or patterns discovered
- Data flow changes between projects
- New skills/commands that would benefit other repos
- Infrastructure changes (deployment, env vars, etc.)

### Step 2: Update the shared knowledge file
Update `C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\CROSS_REPO.md` with any new cross-cutting info.

### Step 3: Sync CROSS_REPO.md copies to all repos
Copy the canonical `CROSS_REPO.md` to each repo's `.claude/CROSS_REPO.md`:
```bash
cp "C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\CROSS_REPO.md" "C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\speed_to_lead\.claude\CROSS_REPO.md"
cp "C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\CROSS_REPO.md" "C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\multichannel-outreach\.claude\CROSS_REPO.md"
cp "C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\CROSS_REPO.md" "C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\contentCreator\.claude\CROSS_REPO.md"
```

### Step 4: Update sibling CLAUDE.md files if needed
If specific project-level knowledge needs to go into a sibling's CLAUDE.md (not just the shared file), update it directly. Examples:
- A new endpoint in speed_to_lead that multichannel-outreach needs to call
- A new content type in contentCreator that speed_to_lead should track

### Step 5: Commit and push to all affected repos
For each repo that changed:
```bash
cd <repo_path>
git add .claude/CROSS_REPO.md CLAUDE.md
git commit -m "Sync cross-repo knowledge from <source_repo>"
git push
```

## What NOT to sync
- Project-specific implementation details (internal functions, local test setup)
- Temporary/experimental features
- Credentials or secrets
- Work-in-progress that isn't ready
