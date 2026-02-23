"""QA Agent for evaluating reply drafts before sending to Slack.

Uses Claude Sonnet to check drafts for:
1. Tone consistency with funnel stage
2. Product/offer context when asked "what do you do?"
3. Stop/no-reply detection ("not interested", "stop messaging")
4. Repetition detection (don't repeat questions already asked)
5. Stage accuracy sanity check
6. Compliance with learned QA guidelines from the database

Scoring: 1.0-5.0
- >= 4.0: pass clean
- 3.0-3.9: pass with flag (yellow warning in Slack)
- < 3.0: block (auto-regen, if still < 3.0 discard)
"""

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

QA_MODEL = "claude-sonnet-4-20250514"
PASS_THRESHOLD = 4.0
FLAG_THRESHOLD = 3.0
MAX_GUIDELINES_PER_STAGE = 15

QA_SYSTEM_PROMPT = """\
You are a QA agent for LinkedIn outreach reply drafts. Your job is to evaluate whether a draft reply is safe and effective to send to a prospect.

You evaluate on these dimensions:
1. **Tone consistency**: Does the reply match the expected tone for this funnel stage?
   - positive_reply: casual, text-message style, like texting a friend. Short. No formal greetings.
   - pitched: professional but warm. Clear value prop.
   - calendar_sent: brief, encouraging. Remove friction.
   - regeneration: re-engage naturally. Don't be pushy.
   - initiated: shouldn't be replying (they haven't replied yet).

2. **Product context**: If the lead asks "what do you do?" or similar, does the reply actually answer with context about LinkedIn client acquisition services? A deflection or vague answer is a failure.

3. **Stop detection**: Does the lead's message indicate they want to stop being contacted? ("not interested", "stop messaging", "unsubscribe", "please don't contact me", "no thanks"). If so, the draft should NOT be sent — flag as should_not_reply.

4. **Repetition**: Does the draft repeat questions or talking points already covered in the conversation history?

5. **Stage accuracy**: Does the detected funnel stage make sense given the conversation content?

Respond with ONLY a JSON object (no markdown fences):
{
  "score": <float 1.0-5.0>,
  "verdict": "<pass|flag|block>",
  "issues": [
    {"type": "<tone|product|stop_detection|repetition|stage_accuracy|guideline>", "detail": "<explanation>", "severity": "<low|medium|high>"}
  ],
  "should_not_reply": <true|false>,
  "reasoning": "<1-2 sentence summary>"
}

Rules for scoring:
- 5.0: Perfect. No issues at all.
- 4.0-4.9: Minor issues that won't affect the conversation.
- 3.0-3.9: Issues that a human should review but the draft is salvageable.
- 2.0-2.9: Significant issues. Should be regenerated.
- 1.0-1.9: Critical issues. Must not be sent (wrong tone, missing product context when asked, or should_not_reply).

If should_not_reply is true, score must be 1.0 regardless of other factors.\
"""


@dataclass
class QAIssue:
    """A single issue found by the QA agent."""

    type: str
    detail: str
    severity: str  # low, medium, high


@dataclass
class QAResult:
    """Result from QA evaluation of a draft."""

    score: float
    verdict: str  # pass, flag, block
    issues: list[QAIssue] = field(default_factory=list)
    should_not_reply: bool = False
    reasoning: str = ""
    model: str = ""
    cost_usd: Decimal = Decimal("0")
    raw_response: str = ""


class QAAgentError(Exception):
    """Raised when the QA agent fails."""

    pass


def _build_qa_prompt(
    lead_name: str,
    lead_message: str,
    ai_draft: str,
    detected_stage: str,
    stage_reasoning: str | None = None,
    conversation_history: list[dict] | None = None,
    guidelines: list[dict] | None = None,
) -> str:
    """Build the user prompt for QA evaluation."""
    parts = []

    parts.append(f"## Lead: {lead_name}")
    parts.append(f"## Detected Stage: {detected_stage}")
    if stage_reasoning:
        parts.append(f"## Stage Reasoning: {stage_reasoning}")

    if conversation_history:
        parts.append("\n## Conversation History:")
        for msg in conversation_history:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            parts.append(f"**{role}**: {content}")

    parts.append(f"\n## Lead's Latest Message:\n{lead_message}")
    parts.append(f"\n## AI Draft Reply:\n{ai_draft}")

    if guidelines:
        parts.append("\n## Active QA Guidelines:")
        for i, g in enumerate(guidelines[:MAX_GUIDELINES_PER_STAGE], 1):
            gtype = g.get("guideline_type", "rule")
            content = g.get("content", "")
            parts.append(f"{i}. [{gtype.upper()}] {content}")

    parts.append("\nEvaluate this draft and respond with the JSON object.")

    return "\n".join(parts)


def _estimate_cost(input_tokens: int, output_tokens: int) -> Decimal:
    """Estimate cost for Claude Sonnet call.

    Sonnet pricing: $3/M input, $15/M output (as of 2025).
    """
    input_cost = Decimal(str(input_tokens)) * Decimal("0.000003")
    output_cost = Decimal(str(output_tokens)) * Decimal("0.000015")
    return (input_cost + output_cost).quantize(Decimal("0.000001"))


async def load_guidelines_for_stage(session, stage: str) -> list[dict]:
    """Load active QA guidelines for a given stage from the database.

    Args:
        session: Database session.
        stage: Funnel stage string (e.g., "positive_reply") or "all".

    Returns:
        List of guideline dicts with guideline_type and content.
    """
    from sqlalchemy import select, or_

    from app.models import QAGuideline

    result = await session.execute(
        select(QAGuideline)
        .where(
            QAGuideline.is_active.is_(True),
            or_(QAGuideline.stage == stage, QAGuideline.stage == "all"),
        )
        .order_by(QAGuideline.occurrences.desc())
        .limit(MAX_GUIDELINES_PER_STAGE)
    )
    guidelines = result.scalars().all()

    return [
        {
            "guideline_type": g.guideline_type.value if hasattr(g.guideline_type, 'value') else str(g.guideline_type),
            "content": g.content,
        }
        for g in guidelines
    ]


async def qa_check_draft(
    lead_name: str,
    lead_message: str,
    ai_draft: str,
    detected_stage: str,
    stage_reasoning: str | None = None,
    conversation_history: list[dict] | None = None,
    guidelines: list[dict] | None = None,
) -> QAResult:
    """Run QA evaluation on a draft reply.

    Args:
        lead_name: Name of the lead.
        lead_message: The lead's most recent message.
        ai_draft: The AI-generated draft reply.
        detected_stage: The detected funnel stage.
        stage_reasoning: Reasoning for stage detection.
        conversation_history: Previous messages.
        guidelines: Active QA guidelines (loaded from DB).

    Returns:
        QAResult with score, verdict, issues, and cost.

    Raises:
        QAAgentError: If the API call fails or response parsing fails.
    """
    user_prompt = _build_qa_prompt(
        lead_name=lead_name,
        lead_message=lead_message,
        ai_draft=ai_draft,
        detected_stage=detected_stage,
        stage_reasoning=stage_reasoning,
        conversation_history=conversation_history,
        guidelines=guidelines,
    )

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        response = await client.messages.create(
            model=QA_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": user_prompt}],
            system=QA_SYSTEM_PROMPT,
        )

        raw_text = response.content[0].text.strip()
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = _estimate_cost(input_tokens, output_tokens)

    except Exception as e:
        raise QAAgentError(f"Anthropic API error: {e}") from e

    # Parse response
    try:
        clean = raw_text
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        clean = clean.strip()

        data = json.loads(clean)

        score = float(data.get("score", 0))
        verdict = data.get("verdict", "block")
        should_not_reply = data.get("should_not_reply", False)
        reasoning = data.get("reasoning", "")

        issues = []
        for issue_data in data.get("issues", []):
            issues.append(QAIssue(
                type=issue_data.get("type", "unknown"),
                detail=issue_data.get("detail", ""),
                severity=issue_data.get("severity", "medium"),
            ))

        # Override verdict based on score thresholds
        if should_not_reply:
            score = 1.0
            verdict = "block"
        elif score >= PASS_THRESHOLD:
            verdict = "pass"
        elif score >= FLAG_THRESHOLD:
            verdict = "flag"
        else:
            verdict = "block"

        return QAResult(
            score=score,
            verdict=verdict,
            issues=issues,
            should_not_reply=should_not_reply,
            reasoning=reasoning,
            model=QA_MODEL,
            cost_usd=cost,
            raw_response=raw_text,
        )

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise QAAgentError(
            f"Failed to parse QA response: {e}. Raw: {raw_text[:300]}"
        ) from e


async def qa_check_with_regen(
    lead_name: str,
    lead_message: str,
    ai_draft: str,
    detected_stage: str,
    stage_reasoning: str | None = None,
    conversation_history: list[dict] | None = None,
    lead_context: dict | None = None,
    guidelines: list[dict] | None = None,
) -> tuple[QAResult, str]:
    """Run QA check with automatic regeneration if draft is blocked.

    If the initial QA score is < 3.0, regenerates the draft with QA feedback
    and re-scores. If the regen also scores < 3.0, returns block verdict.

    Args:
        lead_name: Name of the lead.
        lead_message: The lead's most recent message.
        ai_draft: The AI-generated draft reply.
        detected_stage: The detected funnel stage.
        stage_reasoning: Reasoning for stage detection.
        conversation_history: Previous messages.
        lead_context: Lead context for regeneration.
        guidelines: Active QA guidelines.

    Returns:
        Tuple of (QAResult, final_draft_text). The draft text may be
        the original or a regenerated version.
    """
    # First QA check
    qa_result = await qa_check_draft(
        lead_name=lead_name,
        lead_message=lead_message,
        ai_draft=ai_draft,
        detected_stage=detected_stage,
        stage_reasoning=stage_reasoning,
        conversation_history=conversation_history,
        guidelines=guidelines,
    )

    # If passing or flagged, return as-is
    if qa_result.verdict in ("pass", "flag"):
        return qa_result, ai_draft

    # If should_not_reply, don't bother regenerating
    if qa_result.should_not_reply:
        logger.info(f"QA blocked draft for {lead_name}: should_not_reply detected")
        return qa_result, ai_draft

    # Blocked — attempt regeneration with QA feedback
    logger.info(
        f"QA blocked draft for {lead_name} (score={qa_result.score}). "
        f"Attempting regeneration with feedback."
    )

    try:
        from app.services.deepseek import generate_reply_draft

        # Build corrective feedback from QA issues
        feedback_lines = ["QA FEEDBACK - Fix these issues in your reply:"]
        for issue in qa_result.issues:
            feedback_lines.append(f"- [{issue.severity.upper()}] {issue.type}: {issue.detail}")

        # Add feedback to lead_context for the regeneration
        regen_context = dict(lead_context or {})
        regen_context["qa_feedback"] = "\n".join(feedback_lines)

        regen_result = await generate_reply_draft(
            lead_name=lead_name,
            lead_message=lead_message,
            conversation_history=conversation_history,
            lead_context=regen_context,
        )

        # Re-score the regenerated draft
        regen_qa = await qa_check_draft(
            lead_name=lead_name,
            lead_message=lead_message,
            ai_draft=regen_result.reply,
            detected_stage=detected_stage,
            stage_reasoning=stage_reasoning,
            conversation_history=conversation_history,
            guidelines=guidelines,
        )

        # Accumulate cost from both QA calls
        regen_qa.cost_usd += qa_result.cost_usd

        if regen_qa.verdict == "block":
            logger.info(
                f"Regen also blocked for {lead_name} (score={regen_qa.score}). Discarding."
            )

        return regen_qa, regen_result.reply

    except Exception as e:
        logger.error(f"Regeneration failed for {lead_name}: {e}", exc_info=True)
        # Return original block result
        return qa_result, ai_draft
