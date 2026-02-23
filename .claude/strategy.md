# Growth Strategy & Prioritization

## Current Funnel (2026-02-21)

```
1,723 Prospects
  270 Conversations      (15.7% reply rate)
   63 Positive Reply     (23% of conversations)
    9 Pitched            (14% of positive replies — BOTTLENECK)
    3 Calendar Sent
    2 Booked
   93 Pending drafts (unanswered)
```

## Current Focus (Phase 1: Fix the Leak)

1. **Fix AI draft quality** — in progress (separate instance). 0/136 drafts were usable due to 4 bugs (wrong message roles, no prospect context, generic prompts, regen bug). Fix ships this week.
2. **Work the backlog** — regenerate and send the 93 pending drafts once fix is live.
3. **Reply fast** — speed to lead on incoming responses.
4. **Content 5 posts/week** — ongoing habit (was 2 last week, ramping to 5). Runs in parallel, compounds over time.

## Next Up (Phase 1.5: QA Layer)

Once draft generation quality is solid (prompts tuned, principles working, dynamic examples shipping good results), add the automated QA agent:

- **What:** `app/services/qa_agent.py` — scores each draft 1-5, issues pass/flag/block verdict, auto-regenerates below threshold, blocks truly bad drafts from Slack
- **Why:** Catches the drafts that slip through prompt improvements. Even good prompts produce occasional bad outputs — QA is the safety net.
- **Scaffolding already in place:** `main.py:569-628` imports and calls `qa_check_with_regen()`, handles scoring/blocking, stores QA fields on Draft. Just needs the module built.
- **Blocked on:** Prompt quality. No point scoring garbage — need generation to be good enough that QA has something meaningful to evaluate. Use `/test-prompts` to validate generation quality first.
- **DB fields needed:** `qa_score`, `qa_verdict`, `qa_issues`, `qa_model`, `qa_cost_usd` on Draft model (Alembic migration required)

## Next Up (Phase 2: Optimize Inputs)

Once the reply pipeline is flowing and replies→pitched→booked converts:

### 1. Engagement / Commenting
- **What:** Commenting on prospects' posts before/after connection request
- **Why:** Warms prospects, increases accept rate and reply quality. Closer to mid-funnel — affects the quality of conversations, not just volume.
- **Time cost:** 5-10 hrs/week

### 2. A/B Test Initial Messages
- **What:** Systematically test different opening messages — hooks, tones, angles, lengths
- **Why:** Initial message determines reply rate AND reply quality. Small lift compounds across all 1,700+ prospects. But this is top-of-funnel — need full-funnel data first to know which openers produce replies that actually convert, not just reply rate.
- **Time cost:** Low (swap message templates in HeyReach, track results)
- **When:** Heavy testing once you have enough full-funnel data to measure real impact

## Later Levers (Phase 3: Scale Volume)

Only after bottom-of-funnel converts reliably:

### 3. Scale Connection Volume
- **What:** Increase HeyReach from 30/day to 40-50/day
- **Why:** More top-of-funnel at same conversion rates doubles downstream
- **Time cost:** Near-zero (HeyReach config change)
- **When:** Only after you can handle the increased reply volume

### 4. Improve Follow-Up Docs
- **What:** Two Google Docs sent in automated HeyReach follow-ups
  - "B2B Scale Engine" (3-pillar framework, $25k/mo case study)
  - "Differentiation Framework" (story-driven, voice-note outreach, Travis case study)
- **Current state:** Fine — generating 15.7% reply rate already. Polishing is marginal.
- **When:** Once reply→pitched→booked is healthy

### 5. Lead Magnets / New Content Assets
- **What:** Additional downloadable resources for outreach sequences
- **Why:** Can improve reply rate and position authority
- **Time cost:** Medium (creation + design)

### 6. Buying Signal Targeting
- **What:** A/B test already running — standard DM vs buying-signal DM
- **Why:** Better targeting = higher positive reply rate
- **Time cost:** Near-zero, already automated
- **When:** Check A/B results after sufficient sample size, then double down on winner

## Decision Framework

Used Dickey Bush framework: score each lever on Impact (1-5) and Effort (1-5).

**Rule of thumb:** Don't optimize top-of-funnel until bottom-of-funnel converts. Pouring more water into a leaky bucket wastes volume. Fix the leak first, then scale.
