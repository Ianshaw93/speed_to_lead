# Claude Code Best Practices Discovery

Research the latest Claude Code best practices from Anthropic and suggest improvements to workflow and CLAUDE.md files.

## Instructions

### 1. Search for Latest Best Practices

Use web search to find recent Claude Code tips and best practices from Anthropic developers:

```
Search queries to run:
- "Claude Code best practices 2025 2026"
- "Boris Cherny Claude Code tips" (Anthropic developer)
- "Anthropic Claude Code CLAUDE.md examples"
- "Claude Code workflow optimization"
- "site:github.com/anthropics claude-code examples"
```

Look for:
- Official Anthropic blog posts and documentation
- Tips from Anthropic developers (Boris Cherny, etc.)
- Community best practices and examples
- CLAUDE.md examples from open source projects

### 2. Analyze Current Setup

Read and analyze the user's current CLAUDE.md files:

**Global instructions:**
```
~/.claude/CLAUDE.md
```

**Project instructions:**
```
./CLAUDE.md (current project)
```

Identify:
- What's working well
- What could be improved
- Missing best practices
- Redundant or outdated instructions

### 3. Research Key Topics

Search for best practices on these specific topics:
- Effective prompting patterns for CLAUDE.md
- Project context organization
- Tool usage optimization
- Memory and context management
- Multi-project workflows
- Custom commands/skills
- Subagent patterns

### 4. Generate Recommendations

Provide a structured report:

```markdown
## Best Practices Report

### Recent Discoveries
- [List new tips/practices found from Anthropic sources]

### Current Setup Analysis

#### Global CLAUDE.md
- Strengths: ...
- Suggested additions: ...
- Suggested removals: ...

#### Project CLAUDE.md
- Strengths: ...
- Suggested additions: ...
- Suggested removals: ...

### Recommended Changes

#### High Priority
1. [Change with rationale]

#### Medium Priority
1. [Change with rationale]

#### Optional Enhancements
1. [Change with rationale]

### Example Snippets
[Provide copy-paste ready snippets for suggested changes]

### Sources
- [Links to sources found]
```

### 5. Optional: Apply Changes

If the user approves, help implement the suggested changes:
- Edit CLAUDE.md files
- Create new custom commands
- Update workflow configurations

## Notes

- Focus on actionable, specific recommendations
- Prioritize official Anthropic guidance over community tips
- Consider the user's specific project context
- Don't suggest changes that conflict with existing working patterns
