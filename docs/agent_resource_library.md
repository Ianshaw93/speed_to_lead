# Agent Resource Library — Design Document

## Problem

The current draft generation system uses hardcoded stage-specific prompts (`app/prompts/stages/*.py`). There's no way for agents to reference a growing body of background knowledge — things like the "frame" to maintain in conversations, what tone shifts worked, objection-handling playbooks, industry-specific talking points, or learnings from past conversations.

We need a **resource library** that:

1. Starts small (10-20 docs) but can scale to hundreds
2. Is selectively used — agents pull relevant docs, not everything every time
3. Is updatable by a QA agent that reviews conversation outcomes
4. Is accessible from the draft-generation agent AND the QA agent
5. Doesn't bloat every LLM call with unnecessary context

## Current Architecture

```
HeyReach webhook → stage_detector (DeepSeek) → stage-specific prompt → draft → Slack approval
```

Prompts live in `app/prompts/stages/` as Python files with `SYSTEM_PROMPT` and `build_user_prompt()`. Context comes from conversation history + lead info. There is **no QA agent** yet — drafts go straight to Slack for human approval.

## Approaches Evaluated

### 1. File-based (markdown in repo)

Store docs as `.md` files in `resources/`, load on demand.

| Pros | Cons |
|------|------|
| Simple, git-versioned | No programmatic search or filtering |
| Easy to edit by hand | All 3 repos need filesystem access |
| No infra changes | Can't be updated by QA agent at runtime |
| Great for bootstrapping | No usage tracking |

**Verdict:** Good for initial content authoring, but doesn't support runtime updates by a QA agent or selective retrieval.

### 2. DB table with category/tag-based retrieval

Store docs in a `resource_docs` PostgreSQL table with category, tags, and content. Agents query by category + stage to get relevant docs.

| Pros | Cons |
|------|------|
| Accessible from all 3 repos (shared DB) | No semantic search — relies on good categorization |
| QA agent can CRUD docs programmatically | Need to maintain tags/categories |
| Versionable (track updates) | Agent needs to know what categories exist |
| Usage/effectiveness tracking built-in | |
| Simple to implement | |

**Verdict:** The right starting point. Matches our existing stack, supports all requirements.

### 3. DB + pgvector (RAG-style semantic search)

Same as #2 but add vector embeddings. Agent queries with the conversation context, gets semantically relevant docs back.

| Pros | Cons |
|------|------|
| "Use when relevant" happens automatically | Requires pgvector extension on Railway |
| Scales to large libraries naturally | Embedding cost per doc + per query |
| No manual categorization needed | More complex implementation |
| Most sophisticated retrieval | Railway needs pgvector template (not default Postgres) |

**Verdict:** The ideal end-state, but premature for a library that starts at 10-20 docs. Category-based retrieval is sufficient initially. **Design for this as an upgrade path.**

### 4. External vector DB (Pinecone, Weaviate, etc.)

| Pros | Cons |
|------|------|
| Purpose-built for vector search | Another service to manage and pay for |
| Best performance at scale | Overkill for < 1000 docs |

**Verdict:** Unnecessary complexity for our scale.

---

## Recommended Approach: DB Table + Category Retrieval (pgvector-ready)

### Phase 1 — Database Table (implement now)

#### Model: `ResourceDoc`

```python
class ResourceDocCategory(str, enum.Enum):
    FRAME = "frame"                    # Conversational frame/persona to maintain
    OBJECTION = "objection"            # Objection handling playbooks
    TONE = "tone"                      # Tone & style guidelines
    INDUSTRY = "industry"              # Industry-specific talking points
    QUALIFYING = "qualifying"          # Qualifying question strategies
    CASE_STUDY = "case_study"          # Success stories / social proof
    ANTI_PATTERN = "anti_pattern"      # What NOT to do (learned from failures)
    PLAYBOOK = "playbook"             # Stage-specific conversation playbooks
    LEARNING = "learning"              # Insights from QA review sessions

class ResourceDoc(Base):
    __tablename__ = "resource_docs"

    id: UUID
    title: str                         # "The Ian Frame" / "Objection: Too Busy"
    category: ResourceDocCategory
    content: str                       # The actual document (markdown)
    tags: list[str]                    # ["positive_reply", "rapport", "casual"]
    applicable_stages: list[str]       # ["positive_reply", "pitched"] — which funnel stages this applies to
    priority: int                      # 1-10, higher = more important
    is_active: bool                    # Soft delete / disable
    effectiveness_score: float | None  # QA agent can rate docs over time
    usage_count: int                   # How many times agents have pulled this doc
    last_used_at: datetime | None
    created_by: str                    # "human" / "qa_agent"
    updated_by: str
    version: int                       # Increment on update for audit trail
    created_at: datetime
    updated_at: datetime
    # Phase 2: embedding: Vector(1536) | None  # For pgvector later
```

#### How Agents Use It

**Draft Agent (every message):**
1. Gets a lightweight manifest: list of `(id, title, category, applicable_stages, priority)` — costs ~200 tokens for 20 docs
2. The stage-specific system prompt includes a line: *"You have access to a resource library. If the conversation situation calls for it, reference the following resources:"*
3. Resources matching the current `funnel_stage` are automatically included (filtered by `applicable_stages`)
4. Only high-priority docs (priority >= 7) are auto-included; lower priority docs appear in the manifest for the agent to request

**QA Agent (batch review, e.g. early morning):**
1. Full access to all resource docs
2. Reviews recent conversations: approved drafts, rejected drafts, conversation outcomes
3. Can create new docs (e.g., spotted a pattern that works)
4. Can update effectiveness scores
5. Can flag anti-patterns from failed conversations
6. Can suggest prompt upgrades based on patterns

#### Retrieval Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    Draft Generation                          │
│                                                              │
│  1. Detect stage (existing)                                  │
│  2. Query resource_docs WHERE:                               │
│     - is_active = true                                       │
│     - applicable_stages contains current_stage               │
│     - priority >= threshold (e.g., 7 for auto-include)       │
│  3. Build prompt:                                            │
│     - Stage system prompt (existing)                         │
│     - Auto-included resource docs (high priority, relevant)  │
│     - Manifest of other available docs (titles only)         │
│     - Conversation history + lead context (existing)         │
│  4. Generate draft                                           │
│  5. Increment usage_count on included docs                   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    QA Agent (batch)                           │
│                                                              │
│  1. Load ALL resource docs                                   │
│  2. Load recent conversations (last 24h):                    │
│     - Approved drafts + what was sent                        │
│     - Rejected drafts + why                                  │
│     - Conversation outcomes (did they progress in funnel?)   │
│  3. Analyze patterns:                                        │
│     - What resource docs were used in successful convos?     │
│     - What situations had no applicable resource doc?        │
│     - What drafts got rejected — is there a pattern?         │
│  4. Actions:                                                 │
│     - Create new ResourceDoc for spotted patterns            │
│     - Update effectiveness_score on existing docs            │
│     - Flag anti-patterns                                     │
│     - Suggest prompt changes (logged, not auto-applied)      │
└─────────────────────────────────────────────────────────────┘
```

#### Token Budget

The key constraint is not bloating every draft call. Budget:

| Component | Tokens (approx) |
|-----------|-----------------|
| Stage system prompt | ~300-500 |
| Conversation history | ~200-800 |
| Lead context | ~100-200 |
| **Auto-included resource docs** | **~500-1500** (2-4 docs, ~300 tokens each) |
| **Doc manifest** | **~200** (titles only for remaining docs) |
| **Total** | **~1300-3200** |

This is well within DeepSeek's context window and keeps costs low. The key insight: **most docs don't need to be included every time**. The agent gets titles, and the high-priority stage-relevant ones are auto-injected.

### Phase 2 — pgvector Upgrade (when library exceeds ~50 docs)

When category-based filtering becomes insufficient:

1. Add pgvector extension to Railway Postgres (Railway supports this via one-click templates)
2. Add `embedding` column to `resource_docs`
3. Generate embeddings when docs are created/updated (using DeepSeek or OpenAI embeddings)
4. Replace category-based retrieval with semantic similarity search:
   - Embed the conversation context
   - Find top-K most relevant docs
   - Use those instead of category filtering

The table schema is already designed to accommodate this — just add the `embedding` column.

### Phase 3 — Self-Improving System

Once both the QA agent and resource library are working:

1. **Effectiveness tracking**: Correlate resource doc usage with conversation outcomes (did the lead progress in the funnel?)
2. **Auto-deprecation**: Docs with low effectiveness scores get flagged for review
3. **A/B testing**: QA agent can create alternative versions of docs, and the system randomly assigns them to measure impact
4. **Prompt evolution**: QA agent proposes changes to stage prompts based on resource doc patterns, logged for human review

---

## API Endpoints

```
GET  /admin/resources                    — List all resource docs (with filtering)
GET  /admin/resources/{id}               — Get single doc
POST /admin/resources                    — Create doc (human or QA agent)
PUT  /admin/resources/{id}               — Update doc
GET  /admin/resources/for-stage/{stage}  — Get docs applicable to a funnel stage
POST /admin/resources/{id}/track-usage   — Increment usage counter
```

## Initial Resource Docs to Create

These would be the first docs to seed the library:

1. **"The Ian Frame"** (category: `frame`, priority: 9) — The conversational persona: casual, text-message style, genuinely curious, not salesy
2. **"Qualifying Sequence"** (category: `qualifying`, priority: 8) — The progression of qualifying questions and when to use each
3. **"Pitch Timing Rules"** (category: `playbook`, priority: 8) — When it's appropriate to transition from rapport to pitch
4. **"Objection: Too Busy"** (category: `objection`, priority: 7) — Handling "I don't have time" responses
5. **"Objection: Not Interested"** (category: `objection`, priority: 7) — Graceful exits that leave doors open
6. **"Anti-Pattern: Being Too Formal"** (category: `anti_pattern`, priority: 8) — Examples of overly formal drafts and what they should look like instead
7. **"Anti-Pattern: Premature Pitch"** (category: `anti_pattern`, priority: 8) — Jumping to pitch before qualifying

## Implementation Order

1. Create Alembic migration for `resource_docs` table
2. Add `ResourceDoc` model + `ResourceDocCategory` enum
3. Add CRUD endpoints
4. Add `get_resources_for_stage()` utility function
5. Modify `build_user_prompt()` in each stage to accept + format resource docs
6. Modify `DeepSeekClient.generate_with_stage()` to fetch and inject relevant docs
7. Seed initial resource docs
8. Build QA agent service (separate piece of work)

## File Locations

```
app/models.py                          — ResourceDoc model + enum
app/schemas.py                         — Pydantic schemas for API
app/routers/resources.py               — CRUD endpoints
app/services/resource_library.py       — Retrieval logic
app/prompts/utils.py                   — build_resource_section() helper
alembic/versions/xxx_add_resource_docs.py — Migration
```
