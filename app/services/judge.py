"""Claude Sonnet judge service for evaluating LinkedIn reply draft quality.

Scores drafts on 5 dimensions and provides actionable feedback for revision.
Falls back gracefully if the Anthropic API is unavailable.
"""

import json
import logging
from dataclasses import dataclass, field

import anthropic

from app.config import settings
from app.prompts.judge import (
    DIMENSION_WEIGHTS,
    JUDGE_SYSTEM_PROMPT,
    build_judge_prompt,
)

logger = logging.getLogger(__name__)

SCORE_THRESHOLD = 4.0
MAX_REVISIONS = 1


class JudgeError(Exception):
    """Raised when the judge service fails (API error, parse error, etc.)."""

    pass


@dataclass
class JudgeResult:
    """Result from judging a draft reply."""

    scores: dict[str, float]
    weighted_score: float
    feedback: str
    raw_response: str = ""


def compute_weighted_score(scores: dict[str, float]) -> float:
    """Compute weighted average from dimension scores.

    Args:
        scores: Dict mapping dimension name to score (1-5).

    Returns:
        Weighted average score.
    """
    total = 0.0
    for dimension, weight in DIMENSION_WEIGHTS.items():
        total += scores.get(dimension, 0.0) * weight
    return round(total, 2)


async def judge_draft(
    lead_name: str,
    lead_message: str,
    ai_draft: str,
    conversation_history: list[dict] | None = None,
    lead_context: dict | None = None,
) -> JudgeResult:
    """Judge a draft reply using Claude Sonnet.

    Args:
        lead_name: Name of the lead.
        lead_message: The lead's most recent message.
        ai_draft: The AI-generated draft reply to evaluate.
        conversation_history: Previous messages in the conversation.
        lead_context: Optional lead context (company, title, etc.).

    Returns:
        JudgeResult with scores, weighted score, and feedback.

    Raises:
        JudgeError: If the API call fails or response can't be parsed.
    """
    user_prompt = build_judge_prompt(
        lead_name=lead_name,
        lead_message=lead_message,
        ai_draft=ai_draft,
        conversation_history=conversation_history,
        lead_context=lead_context,
    )

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
            system=JUDGE_SYSTEM_PROMPT,
        )

        raw_text = response.content[0].text.strip()
    except Exception as e:
        raise JudgeError(f"Anthropic API error: {e}") from e

    # Parse JSON response
    try:
        # Strip markdown fences if present
        clean = raw_text
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        clean = clean.strip()

        data = json.loads(clean)
        scores = data.get("scores", {})
        feedback = data.get("feedback", "")

        # Validate all dimensions present
        for dim in DIMENSION_WEIGHTS:
            if dim not in scores:
                raise JudgeError(f"Missing dimension in response: {dim}")
            scores[dim] = float(scores[dim])

        weighted = compute_weighted_score(scores)

        return JudgeResult(
            scores=scores,
            weighted_score=weighted,
            feedback=feedback,
            raw_response=raw_text,
        )

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise JudgeError(f"Failed to parse judge response: {e}. Raw: {raw_text[:200]}") from e
