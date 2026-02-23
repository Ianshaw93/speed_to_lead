# Test Prompt Quality

Test AI draft quality by running current prompts against real past conversations. Shows side-by-side comparison of what was actually sent vs what the AI would generate now.

## Instructions

### 1. Run the test harness

Run `scripts/test_prompts.py` against the production database. This script:
- Pulls approved conversations at each funnel stage from the DB
- Re-generates drafts using the **current** prompts (with principles + dynamic examples)
- Displays a side-by-side comparison for each conversation

**Default run (3 conversations per stage, all stages):**
```bash
python scripts/test_prompts.py --output .tmp/prompt_test_results.md
```

**Test a specific stage with more examples:**
```bash
python scripts/test_prompts.py --stage positive_reply --count 5 --output .tmp/prompt_test_results.md
```

**Compare with vs without dynamic examples:**
```bash
python scripts/test_prompts.py --stage positive_reply --count 3 --output .tmp/with_examples.md
python scripts/test_prompts.py --stage positive_reply --count 3 --no-dynamic-examples --output .tmp/without_examples.md
```

### 2. Review results

Read the output file and present results to the user in a structured format:

For each test case, show:
1. **Lead's message** — what the prospect said
2. **What was actually sent** — the approved/edited human response
3. **What AI generates now** — the new AI draft with current prompts
4. **Stage detection** — was the stage detected correctly?

### 3. Analyze quality

After presenting results, analyze:
- **Tone match**: Does the AI draft match the casual, text-message style?
- **Length match**: Are AI drafts the right length (2-3 short messages)?
- **Qualifying intent**: Is the AI working toward qualifying questions naturally?
- **Adaptability**: Does the AI respond to what the lead actually said, or use generic patterns?
- **Dynamic examples impact**: If comparing with/without examples, note the difference

### 4. Suggest improvements

Based on the analysis:
- Identify specific prompt changes that would improve quality
- Note any systematic issues (too formal, too long, wrong questions, etc.)
- Suggest new few-shot examples to add if patterns are missing
- Flag any conversations where the AI draft is actually better than what was sent

## When to use

- After modifying any prompt in `app/prompts/stages/`
- After changing `app/prompts/principles.py`
- After modifying the example retriever
- When tuning the system and want to see the impact of changes
- Before deploying prompt changes to production

## Environment requirements

- `DATABASE_URL` must point to the production Railway database
- `DEEPSEEK_API_KEY` must be set
- Both should be in `.env` file
