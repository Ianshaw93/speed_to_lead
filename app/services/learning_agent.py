"""Learning Agent for extracting insights from human edits to AI drafts.

Two scheduled tasks:
1. Daily (2am UK): Analyze drafts where human edited the AI draft in the last 24h.
   Extract learnings about what changed and why → store in draft_learnings table.

2. Weekly (Saturday 3am UK): Consolidate learnings into qa_guidelines.
   Patterns seen ≥3 times across different conversations → promote to guideline.
   Prune guidelines not reinforced in 30 days.
   Post Slack summary of what was learned.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import anthropic
from sqlalchemy import and_, func, select

from app.config import settings
from app.models import (
    Draft,
    DraftLearning,
    FunnelStage,
    GuidelineType,
    LearningType,
    QAGuideline,
)

logger = logging.getLogger(__name__)

LEARNING_MODEL = "claude-sonnet-4-20250514"
MIN_OCCURRENCES_FOR_GUIDELINE = 3
GUIDELINE_PRUNE_DAYS = 30
MAX_GUIDELINES_PER_STAGE = 15

LEARNING_SYSTEM_PROMPT = """\
You analyze differences between AI-generated LinkedIn reply drafts and human-edited versions.

Your job: identify WHAT changed and WHY the human made that change.

Categorize each learning into one of these types:
- **tone**: The human changed the tone (e.g., too formal → casual, too pushy → gentle)
- **content**: The human added/removed specific content (e.g., product details, value prop)
- **structure**: The human restructured the message (e.g., shortened, reordered, removed questions)
- **skip_detection**: The human skipped/rejected because the lead shouldn't be contacted
- **product_knowledge**: The human added specific product/service information the AI didn't have

Respond with ONLY a JSON object:
{
  "learnings": [
    {
      "type": "<tone|content|structure|skip_detection|product_knowledge>",
      "original_snippet": "<relevant part of original>",
      "corrected_snippet": "<what the human changed it to>",
      "explanation": "<why this change was made, in 1-2 sentences>",
      "confidence": <0.0-1.0>
    }
  ]
}

If the edit is trivial (typo fix, minor punctuation), return {"learnings": []}.\
"""

CONSOLIDATION_SYSTEM_PROMPT = """\
You consolidate individual learnings from human edits into actionable QA guidelines.

Given a set of learnings grouped by type and stage, identify patterns that appear across multiple conversations.

For each pattern, create a guideline that the QA agent can use to evaluate future drafts.

Respond with ONLY a JSON object:
{
  "guidelines": [
    {
      "stage": "<funnel_stage or 'all'>",
      "guideline_type": "<do|dont|example|tone_rule>",
      "content": "<the guideline text, clear and actionable>",
      "source_count": <number of learnings this is based on>
    }
  ]
}

Only include guidelines based on patterns seen in 3+ different conversations.
Keep guidelines concise and specific. Avoid vague rules.\
"""

# Seed guidelines for known P0/P1 issues
SEED_GUIDELINES = [
    {
        "stage": "all",
        "guideline_type": "do",
        "content": "If lead asks 'what do you do?' or similar, reply MUST include product context about LinkedIn client acquisition services. Never deflect.",
    },
    {
        "stage": "positive_reply",
        "guideline_type": "tone_rule",
        "content": "positive_reply stage must use text-message casual tone, NOT professional assistant tone. Short sentences, like texting a friend.",
    },
    {
        "stage": "all",
        "guideline_type": "dont",
        "content": "If lead says 'not interested', 'stop messaging', 'unsubscribe', or similar, flag as should_not_reply. Do NOT generate a persuasive reply.",
    },
]


def _estimate_cost(input_tokens: int, output_tokens: int) -> Decimal:
    """Estimate cost for Claude Sonnet call."""
    input_cost = Decimal(str(input_tokens)) * Decimal("0.000003")
    output_cost = Decimal(str(output_tokens)) * Decimal("0.000015")
    return (input_cost + output_cost).quantize(Decimal("0.000001"))


async def analyze_edit(
    original_text: str,
    edited_text: str,
    stage: str | None = None,
    conversation_history: list[dict] | None = None,
) -> list[dict]:
    """Analyze the difference between original AI draft and human edit.

    Args:
        original_text: The original AI-generated draft.
        edited_text: The human-edited version.
        stage: Funnel stage for context.
        conversation_history: Conversation context.

    Returns:
        List of learning dicts with type, explanation, confidence.
    """
    parts = [f"## Funnel Stage: {stage or 'unknown'}"]

    if conversation_history:
        parts.append("\n## Conversation Context:")
        for msg in conversation_history[-5:]:  # Last 5 messages for context
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            parts.append(f"**{role}**: {content}")

    parts.append(f"\n## Original AI Draft:\n{original_text}")
    parts.append(f"\n## Human-Edited Version:\n{edited_text}")
    parts.append("\nAnalyze what changed and why.")

    user_prompt = "\n".join(parts)

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        response = await client.messages.create(
            model=LEARNING_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": user_prompt}],
            system=LEARNING_SYSTEM_PROMPT,
        )

        raw_text = response.content[0].text.strip()

        # Parse JSON
        clean = raw_text
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        clean = clean.strip()

        data = json.loads(clean)
        return data.get("learnings", [])

    except Exception as e:
        logger.error(f"Failed to analyze edit: {e}", exc_info=True)
        return []


async def run_daily_learning() -> dict:
    """Analyze human edits from the last 24 hours and store learnings.

    Called by scheduler at 2am UK time daily.

    Returns:
        Dict with counts of drafts analyzed and learnings created.
    """
    from app.database import async_session_factory

    logger.info("Starting daily learning analysis")

    async with async_session_factory() as session:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        # Find drafts where human edited (human_edited_draft is not null)
        result = await session.execute(
            select(Draft)
            .where(
                Draft.human_edited_draft.isnot(None),
                Draft.original_ai_draft.isnot(None),
                Draft.updated_at >= cutoff,
            )
        )
        edited_drafts = result.scalars().all()

        if not edited_drafts:
            logger.info("No edited drafts found in last 24h")
            return {"drafts_analyzed": 0, "learnings_created": 0}

        # Check which drafts already have learnings (skip duplicates)
        existing_draft_ids = set()
        for draft in edited_drafts:
            existing = await session.execute(
                select(DraftLearning.id).where(DraftLearning.draft_id == draft.id).limit(1)
            )
            if existing.scalar_one_or_none():
                existing_draft_ids.add(draft.id)

        drafts_to_analyze = [d for d in edited_drafts if d.id not in existing_draft_ids]

        if not drafts_to_analyze:
            logger.info("All edited drafts already have learnings")
            return {"drafts_analyzed": 0, "learnings_created": 0}

        total_learnings = 0
        for draft in drafts_to_analyze:
            # Get conversation for context
            from app.models import Conversation
            conv_result = await session.execute(
                select(Conversation).where(Conversation.id == draft.conversation_id)
            )
            conversation = conv_result.scalar_one_or_none()
            conv_history = conversation.conversation_history if conversation else None

            # Determine stage
            stage = None
            if conversation and conversation.funnel_stage:
                stage = conversation.funnel_stage.value

            learnings = await analyze_edit(
                original_text=draft.original_ai_draft,
                edited_text=draft.human_edited_draft,
                stage=stage,
                conversation_history=conv_history,
            )

            for learning in learnings:
                learning_type_str = learning.get("type", "content")
                try:
                    learning_type = LearningType(learning_type_str)
                except ValueError:
                    learning_type = LearningType.CONTENT

                dl = DraftLearning(
                    draft_id=draft.id,
                    learning_type=learning_type,
                    original_text=learning.get("original_snippet", draft.original_ai_draft[:200]),
                    corrected_text=learning.get("corrected_snippet", draft.human_edited_draft[:200]),
                    diff_summary=learning.get("explanation", ""),
                    stage=stage,
                    confidence=Decimal(str(learning.get("confidence", 0.5))),
                )
                session.add(dl)
                total_learnings += 1

        await session.commit()

        logger.info(f"Daily learning: analyzed {len(drafts_to_analyze)} drafts, created {total_learnings} learnings")
        return {
            "drafts_analyzed": len(drafts_to_analyze),
            "learnings_created": total_learnings,
        }


async def run_weekly_consolidation() -> dict:
    """Consolidate learnings into QA guidelines. Called Saturday 3am UK time.

    Groups learnings by (stage, type). Patterns seen ≥3 times across different
    conversations → create/update qa_guidelines. Prune inactive guidelines.
    Post Slack summary.

    Returns:
        Dict with counts of guidelines created, updated, and pruned.
    """
    from app.database import async_session_factory

    logger.info("Starting weekly guideline consolidation")

    async with async_session_factory() as session:
        # Get all learnings from last 7 days grouped by stage + type
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        result = await session.execute(
            select(DraftLearning)
            .where(DraftLearning.created_at >= cutoff)
            .order_by(DraftLearning.stage, DraftLearning.learning_type)
        )
        recent_learnings = result.scalars().all()

        if not recent_learnings:
            logger.info("No recent learnings to consolidate")
            return {"created": 0, "updated": 0, "pruned": 0}

        # Group by stage + type for the LLM
        groups: dict[str, list[dict]] = {}
        for learning in recent_learnings:
            key = f"{learning.stage or 'all'}|{learning.learning_type.value}"
            if key not in groups:
                groups[key] = []
            groups[key].append({
                "id": str(learning.id),
                "original": learning.original_text[:200],
                "corrected": learning.corrected_text[:200],
                "explanation": learning.diff_summary,
                "confidence": float(learning.confidence),
            })

        # Build prompt with all learning groups
        parts = ["## Recent Learnings from Human Edits\n"]
        for key, learnings_list in groups.items():
            stage, ltype = key.split("|")
            parts.append(f"### Stage: {stage}, Type: {ltype} ({len(learnings_list)} learnings)")
            for l in learnings_list[:5]:  # Cap at 5 per group
                parts.append(f"- Original: {l['original'][:100]}")
                parts.append(f"  Corrected: {l['corrected'][:100]}")
                parts.append(f"  Why: {l['explanation']}")
            parts.append("")

        user_prompt = "\n".join(parts)

        # Ask Claude to consolidate into guidelines
        try:
            client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            response = await client.messages.create(
                model=LEARNING_MODEL,
                max_tokens=1000,
                messages=[{"role": "user", "content": user_prompt}],
                system=CONSOLIDATION_SYSTEM_PROMPT,
            )

            raw_text = response.content[0].text.strip()
            clean = raw_text
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()

            data = json.loads(clean)
            new_guidelines = data.get("guidelines", [])

        except Exception as e:
            logger.error(f"Failed to consolidate learnings: {e}", exc_info=True)
            return {"created": 0, "updated": 0, "pruned": 0, "error": str(e)}

        # Upsert guidelines
        created = 0
        updated = 0

        for g in new_guidelines:
            if g.get("source_count", 0) < MIN_OCCURRENCES_FOR_GUIDELINE:
                continue

            stage = g.get("stage", "all")
            gtype_str = g.get("guideline_type", "do")
            content = g.get("content", "")

            try:
                gtype = GuidelineType(gtype_str)
            except ValueError:
                gtype = GuidelineType.DO

            # Check for similar existing guideline
            existing = await session.execute(
                select(QAGuideline).where(
                    QAGuideline.stage == stage,
                    QAGuideline.guideline_type == gtype,
                    QAGuideline.is_active.is_(True),
                )
            )
            existing_guidelines = existing.scalars().all()

            # Simple duplicate check: if content is very similar, update occurrences
            matched = False
            for eg in existing_guidelines:
                # Rough similarity: check if >50% of words overlap
                existing_words = set(eg.content.lower().split())
                new_words = set(content.lower().split())
                if existing_words and new_words:
                    overlap = len(existing_words & new_words) / max(len(existing_words), len(new_words))
                    if overlap > 0.5:
                        eg.occurrences += 1
                        eg.updated_at = datetime.now(timezone.utc)
                        updated += 1
                        matched = True
                        break

            if not matched:
                new_g = QAGuideline(
                    stage=stage,
                    guideline_type=gtype,
                    content=content,
                    occurrences=g.get("source_count", 1),
                )
                session.add(new_g)
                created += 1

        # Prune: deactivate guidelines not reinforced in 30 days
        prune_cutoff = datetime.now(timezone.utc) - timedelta(days=GUIDELINE_PRUNE_DAYS)
        stale_result = await session.execute(
            select(QAGuideline).where(
                QAGuideline.is_active.is_(True),
                QAGuideline.updated_at < prune_cutoff,
            )
        )
        stale_guidelines = stale_result.scalars().all()
        pruned = 0
        for sg in stale_guidelines:
            sg.is_active = False
            pruned += 1

        await session.commit()

        # Post Slack summary
        try:
            from app.services.slack import get_slack_bot
            bot = get_slack_bot()
            summary = (
                f"*Weekly QA Learning Consolidation*\n"
                f"- Learnings analyzed: {len(recent_learnings)}\n"
                f"- New guidelines created: {created}\n"
                f"- Existing guidelines reinforced: {updated}\n"
                f"- Stale guidelines pruned: {pruned}"
            )
            await bot.send_confirmation(summary)
        except Exception as e:
            logger.warning(f"Failed to send Slack summary: {e}")

        logger.info(
            f"Weekly consolidation: created={created}, updated={updated}, pruned={pruned}"
        )
        return {"created": created, "updated": updated, "pruned": pruned}


async def seed_initial_guidelines() -> dict:
    """Seed the qa_guidelines table with known P0/P1 issues.

    Safe to call multiple times — skips guidelines that already exist.

    Returns:
        Dict with count of guidelines seeded.
    """
    from app.database import async_session_factory

    async with async_session_factory() as session:
        seeded = 0

        for seed in SEED_GUIDELINES:
            # Check if similar guideline already exists
            existing = await session.execute(
                select(QAGuideline).where(
                    QAGuideline.stage == seed["stage"],
                    QAGuideline.content == seed["content"],
                )
            )
            if existing.scalar_one_or_none():
                continue

            try:
                gtype = GuidelineType(seed["guideline_type"])
            except ValueError:
                gtype = GuidelineType.DO

            g = QAGuideline(
                stage=seed["stage"],
                guideline_type=gtype,
                content=seed["content"],
                occurrences=10,  # High occurrence count so seed guidelines aren't pruned
            )
            session.add(g)
            seeded += 1

        await session.commit()
        logger.info(f"Seeded {seeded} initial QA guidelines")
        return {"seeded": seeded}
