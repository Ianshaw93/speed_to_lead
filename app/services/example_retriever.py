"""Retrieve similar past conversations as dynamic few-shot examples.

Queries approved/sent drafts from the database, ranked by relevance
to the current conversation context. These examples are injected into
the generation prompt so the AI sees real conversations similar to
the one it's drafting for.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Draft, DraftStatus, FunnelStage

logger = logging.getLogger(__name__)


@dataclass
class RetrievedExample:
    """A past conversation example for few-shot prompting."""

    lead_name: str
    lead_message: str  # The lead's message that triggered the draft
    draft_reply: str  # The approved/sent reply (actual_sent_text if edited, else ai_draft)
    company: str | None
    title: str | None
    is_first_reply: bool
    funnel_stage: FunnelStage
    was_edited: bool = False  # True if actual_sent_text differs from ai_draft


async def get_similar_examples(
    stage: FunnelStage,
    lead_context: dict | None,
    current_lead_message: str,
    db: AsyncSession,
    limit: int = 3,
) -> list[RetrievedExample]:
    """Retrieve approved drafts from the same stage, ranked by relevance.

    Args:
        stage: Current funnel stage to filter by.
        lead_context: Current lead context (company, title, is_first_reply, etc.).
        current_lead_message: The lead's current message (for length matching).
        db: Database session.
        limit: Max number of examples to return.

    Returns:
        List of RetrievedExample, most relevant last (for recency bias).
    """
    lead_context = lead_context or {}
    over_fetch = limit * 5  # Fetch more than needed, then rank

    # Query approved drafts at the same funnel stage
    query = (
        select(Draft, Conversation)
        .join(Conversation, Draft.conversation_id == Conversation.id)
        .where(
            Draft.status.in_([DraftStatus.APPROVED.value, "sent"]),
            Conversation.funnel_stage == stage,
        )
        .order_by(Draft.created_at.desc())
        .limit(over_fetch)
    )

    result = await db.execute(query)
    rows = result.all()

    if not rows:
        logger.info(f"No approved examples found for stage {stage.value}")
        return []

    # Extract lead messages from conversation history
    examples = []
    for draft, conversation in rows:
        lead_message = _extract_last_lead_message(conversation.conversation_history)
        if not lead_message:
            continue

        # Use actual_sent_text if available (what was really sent after potential edits)
        # Fall back to ai_draft for older drafts without actual_sent_text
        reply_text = draft.actual_sent_text or draft.ai_draft
        was_edited = (
            draft.actual_sent_text is not None
            and draft.actual_sent_text != draft.ai_draft
        )

        examples.append(
            RetrievedExample(
                lead_name=conversation.lead_name or "Lead",
                lead_message=lead_message,
                draft_reply=reply_text,
                company=None,
                title=None,
                is_first_reply=draft.is_first_reply,
                funnel_stage=stage,
                was_edited=was_edited,
            )
        )

    if not examples:
        return []

    # Rank by relevance heuristics
    ranked = _rank_examples(examples, lead_context, current_lead_message)

    # Return top N, with most relevant LAST (recency bias in LLMs)
    selected = ranked[:limit]
    selected.reverse()

    logger.info(
        f"Retrieved {len(selected)} dynamic examples for stage {stage.value}"
    )
    return selected


def _extract_last_lead_message(
    conversation_history: list[dict] | None,
) -> str | None:
    """Extract the last lead message from conversation history.

    Args:
        conversation_history: List of message dicts with 'role' and 'content'.

    Returns:
        The last lead message content, or None if not found.
    """
    if not conversation_history:
        return None

    for msg in reversed(conversation_history):
        if msg.get("role") == "lead" and msg.get("content"):
            return msg["content"]
    return None


def _rank_examples(
    examples: list[RetrievedExample],
    lead_context: dict,
    current_lead_message: str,
) -> list[RetrievedExample]:
    """Rank examples by relevance to the current conversation.

    Scoring heuristics:
    - is_first_reply match: +3 (critical context match)
    - message length similarity: +2 (short reply vs detailed reply)
    - keyword overlap in lead message: +1 per shared keyword

    Args:
        examples: List of candidate examples.
        lead_context: Current lead's context dict.
        current_lead_message: The current lead's message.

    Returns:
        Examples sorted by score descending (most relevant first).
    """
    current_is_first = lead_context.get("is_first_reply", False)
    current_length = len(current_lead_message)
    current_words = set(current_lead_message.lower().split())
    # Remove very common words for better matching
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "i", "you", "he", "she", "it", "we", "they", "me", "him",
        "her", "us", "them", "my", "your", "his", "its", "our",
        "their", "this", "that", "these", "those", "and", "but",
        "or", "not", "no", "yes", "so", "if", "then", "than",
        "too", "very", "just", "also", "of", "in", "on", "at",
        "to", "for", "with", "from", "by", "as", "into", "about",
    }
    current_keywords = current_words - stop_words

    scored = []
    for ex in examples:
        score = 0.0

        # is_first_reply match (high weight)
        if ex.is_first_reply == current_is_first:
            score += 3.0

        # Message length similarity (penalize large differences)
        ex_length = len(ex.lead_message)
        length_ratio = min(current_length, ex_length) / max(current_length, ex_length, 1)
        score += length_ratio * 2.0

        # Keyword overlap in lead messages
        ex_words = set(ex.lead_message.lower().split()) - stop_words
        overlap = current_keywords & ex_words
        score += len(overlap) * 0.5

        # Prefer unedited examples (AI got it right)
        if not ex.was_edited:
            score += 1.0

        scored.append((score, ex))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    return [ex for _, ex in scored]


def format_examples_for_prompt(examples: list[RetrievedExample]) -> str:
    """Format retrieved examples into a prompt section.

    Args:
        examples: List of RetrievedExample to format.

    Returns:
        Formatted string for injection into the prompt, or empty string.
    """
    if not examples:
        return ""

    lines = [
        "## Similar Past Conversations (for style reference — adapt, don't copy)\n"
    ]

    for i, ex in enumerate(examples, 1):
        # Build context label
        context_parts = []
        if ex.company:
            context_parts.append(ex.company)
        if ex.is_first_reply:
            context_parts.append("first reply")
        else:
            context_parts.append("continuing conversation")
        context_label = ", ".join(context_parts) if context_parts else "conversation"

        lines.append(f"Example {i} ({context_label}):")
        lines.append(f'Lead: "{ex.lead_message}"')

        # Split draft reply into separate messages (each line is a message)
        for reply_line in ex.draft_reply.strip().split("\n"):
            reply_line = reply_line.strip()
            if reply_line:
                lines.append(f'You: "{reply_line}"')

        lines.append("[This was approved and sent]\n")

    return "\n".join(lines)
