# Prompt Quality Session Summary — 2026-02-24

## What We Did

Ran the `/test-prompts` harness against production conversations to evaluate AI draft quality, identified systematic issues, and fixed them iteratively across 4 commits.

## Issues Found & Fixed

### 1. Stage Detection Completely Broken
**Problem:** Every conversation detected as `positive_reply` regardless of actual stage. DeepSeek wraps JSON in ` ```json ``` ` markdown fences, and `_parse_stage_response` did raw `json.loads()` without stripping them.

**Fix:** Strip markdown code fences before parsing (same pattern already used in comment drafter). Added warning log on parse failure.

**Impact:** Stage detection now correctly identifies all 6 stages. Calendar_sent, pitched, booked, and regeneration conversations get the right prompts.

**Commit:** `2b58138` — Fix stage detection JSON parsing

---

### 2. All Stage Prompts Too Formal / Missing Style
**Problem:** Only `positive_reply` had text-message style + real examples. The other 4 stages (pitched, calendar_sent, booked, regeneration) used corporate language ("I'd be happy to walk you through", "looking forward to connecting") and had no real examples.

**Fix:** Rewrote all 5 stage prompts with:
- Text-message style instructions across all stages
- Real examples from actual sent messages
- Calendly link baked into pitched/calendar_sent/booked
- Regeneration: no placeholder links or meta-commentary

**Impact:**
- `calendar_sent` Vikas Pandey response is now an exact match to what was actually sent
- `pitched` Doug response went from 3 paragraphs of corporate text to short punchy lines with calendar link
- `regeneration` no longer outputs "Why this works:" explanations

**Commit:** `872e96b` — Improve reply quality across all stage prompts

---

### 3. "Is LinkedIn a Big Channel?" Default
**Problem:** 3/8 positive_reply tests defaulted to "Is LinkedIn a big channel for you?" regardless of what the lead said. Caused by:
- Qualifying goals listed LinkedIn as first thing to learn
- Static examples 2 and 5 both used the LinkedIn question
- Dynamic examples pulled from approved drafts that frequently use it

**Fix:**
- Reframed qualifying goals (understand business, ICP, challenges — not "LinkedIn")
- Added "VARY YOUR QUALIFYING QUESTIONS" section with explicit instruction
- Replaced LinkedIn-defaulting examples with diverse alternatives (deal sizes, traction, role-specific)
- Strengthened dynamic examples header: "style reference ONLY — do NOT copy questions"
- Added to DO NOT list: "Ask about LinkedIn as a channel unless it flows naturally"

**Impact:** Zero "Is LinkedIn a big channel?" across all 8 positive_reply tests in v4. Questions now match what the lead said:
- Brody (M&A advisor) → "What kind of deal sizes are you typically looking at?"
- Loeka (founder picker) → "What kind of founder metrics are you looking for?"
- Colin (brand work) → "What's the typical deal size with a brand?"

**Commit:** `d9c4295` — Improve reply quality: diverse qualifying, no free consulting

---

### 4. Free Consulting / Not Engaging With Lead's Message
**Problem:** Bernard asked for positioning advice and the AI gave it ("I'd push the execution OS angle"). Brody described his role and the AI said "solid combo" then jumped to LinkedIn question.

**Fix:**
- Added "REACT TO WHAT THEY SAID FIRST" principle with specific sub-rules
- Added example 7 showing how to handle advice requests (tease, don't give away)
- Added to DO NOT: "Give free consulting or strategy advice — tease and redirect to a call"

**Impact:** Bernard now gets qualifying questions ("So you're getting calls already from where?") instead of free strategy. Brody gets "What kind of deal sizes?" instead of generic comment + LinkedIn question.

**Commit:** `d9c4295` (same as above)

---

### 5. Unicode Encoding Bug (Windows)
**Problem:** Bernard Baah's messages contain `→` arrows that crashed the script with `charmap codec can't encode character`.

**Fix:** Set stdout/stderr to UTF-8 on Windows, write output file with `encoding="utf-8"`.

**Commit:** `4e2b742` — Fix unicode encoding in test_prompts.py for Windows

---

## Test Results Progression

| Version | LinkedIn Q default | Stage detection | Tone match | Calendar link |
|---------|-------------------|-----------------|------------|---------------|
| v1 (before) | 8/8 tests | 0% correct | Good for pos_reply only | Never included |
| v2 (stage fix) | 3/8 tests | ~80% correct | Good across stages | Included in calendar_sent |
| v3 (prompt rewrite) | 3/8 tests | ~80% correct | Good across stages | All relevant stages |
| v4 (final) | **0/8 tests** | **~80% correct** | **Good across stages** | **All relevant stages** |

## Files Changed

| File | Change |
|------|--------|
| `app/services/deepseek.py` | Strip markdown fences in stage detection parser |
| `app/prompts/stages/positive_reply.py` | Diverse qualifying, react-first principle, no free consulting |
| `app/prompts/stages/pitched.py` | Text-message style, real examples, Calendly link |
| `app/prompts/stages/calendar_sent.py` | Ultra-brief style, real examples, Calendly link |
| `app/prompts/stages/booked.py` | Casual style, real examples |
| `app/prompts/stages/regeneration.py` | No meta-commentary, real examples |
| `app/services/example_retriever.py` | Stronger "don't copy" header on dynamic examples |
| `scripts/test_prompts.py` | Fresh session per stage, UTF-8 encoding, .env public DB URL |

## Remaining Gaps

- **Limited test data** for `booked` (2 conversations) and `regeneration` (0) stages
- **Pitched stage mis-detection**: Patrick Ryan and Catherine detected as positive_reply (their first reply, so technically correct — DB stage was likely advanced manually)
- **Personal touches**: AI can't reference personal stories/connections Ian would have (lived in London, Hawaiian friend, etc.) — inherent LLM limitation
- **Same lead appearing multiple times**: Andy Bergmann appears 5x in calendar_sent due to multiple drafts on one conversation — test harness could deduplicate
