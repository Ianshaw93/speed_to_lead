"""DeepSeek AI client for generating LinkedIn reply drafts with stage detection."""

import json
import logging
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from app.config import settings
from app.models import FunnelStage
from app.prompts.comment_drafter import (
    SYSTEM_PROMPT as COMMENT_DRAFTER_SYSTEM_PROMPT,
    build_comment_drafter_prompt,
)
from app.prompts.stage_detector import (
    STAGE_DETECTION_SYSTEM_PROMPT,
    build_stage_detection_prompt,
)
from app.prompts.stages import get_stage_prompt

logger = logging.getLogger(__name__)


class DeepSeekError(Exception):
    """Custom exception for DeepSeek API errors."""

    pass


@dataclass
class DraftResult:
    """Result from draft generation including detected stage."""

    detected_stage: FunnelStage
    stage_reasoning: str
    reply: str
    judge_score: float | None = None
    judge_feedback: str | None = None
    revision_count: int = 0


class DeepSeekClient:
    """Client for interacting with the DeepSeek API using OpenAI SDK."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ):
        """Initialize the DeepSeek client.

        Args:
            api_key: DeepSeek API key. Defaults to settings value.
            model: Model to use. Defaults to settings value.
        """
        self._api_key = api_key or settings.deepseek_api_key
        self._model = model or settings.deepseek_model
        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=settings.deepseek_base_url,
        )

    async def detect_stage(
        self,
        lead_name: str,
        lead_message: str,
        conversation_history: list[dict] | None = None,
        lead_context: dict | None = None,
    ) -> tuple[FunnelStage, str]:
        """Detect the funnel stage from conversation.

        Args:
            lead_name: Name of the lead.
            lead_message: The lead's most recent message.
            conversation_history: Previous messages in the conversation.
            lead_context: Optional lead context (company, title, etc.).

        Returns:
            Tuple of (FunnelStage, reasoning string).
        """
        try:
            user_prompt = build_stage_detection_prompt(
                lead_name=lead_name,
                lead_message=lead_message,
                conversation_history=conversation_history,
                lead_context=lead_context,
            )

            messages = [
                {"role": "system", "content": STAGE_DETECTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            completion = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=200,
                temperature=0.3,  # Lower temperature for more consistent stage detection
            )

            if not completion.choices:
                return FunnelStage.POSITIVE_REPLY, "Fallback: empty response"

            content = completion.choices[0].message.content
            return self._parse_stage_response(content)

        except Exception as e:
            # Fallback on any error
            return FunnelStage.POSITIVE_REPLY, f"Fallback due to error: {e}"

    def _parse_stage_response(self, content: str) -> tuple[FunnelStage, str]:
        """Parse the JSON response from stage detection.

        Args:
            content: Raw response content from LLM.

        Returns:
            Tuple of (FunnelStage, reasoning string).
        """
        try:
            # Strip markdown code fences if present (e.g. ```json ... ```)
            clean = content.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()

            data = json.loads(clean)
            stage_str = data.get("detected_stage", "positive_reply")
            reasoning = data.get("reasoning", "")

            # Try to match the stage string to enum
            try:
                stage = FunnelStage(stage_str)
            except ValueError:
                stage = FunnelStage.POSITIVE_REPLY
                reasoning = f"Fallback from unknown stage '{stage_str}': {reasoning}"

            return stage, reasoning

        except json.JSONDecodeError:
            logger.warning(f"Could not parse stage detection response: {content[:200]}")
            return FunnelStage.POSITIVE_REPLY, "Fallback: could not parse JSON response"

    async def generate_with_stage(
        self,
        lead_name: str,
        lead_message: str,
        stage: FunnelStage,
        conversation_history: list[dict] | None = None,
        guidance: str | None = None,
        lead_context: dict | None = None,
        dynamic_examples: str = "",
    ) -> str:
        """Generate a reply using stage-specific prompt.

        Args:
            lead_name: Name of the lead.
            lead_message: The lead's most recent message.
            stage: The detected funnel stage.
            conversation_history: Previous messages in the conversation.
            guidance: Optional user guidance for regeneration.
            lead_context: Optional lead context (company, title, etc.).
            dynamic_examples: Pre-formatted examples from similar past conversations.

        Returns:
            The generated reply text.

        Raises:
            DeepSeekError: If the API call fails or returns empty response.
        """
        try:
            # Get the stage-specific prompt module
            prompt_module = get_stage_prompt(stage)

            user_prompt = prompt_module.build_user_prompt(
                lead_name=lead_name,
                lead_message=lead_message,
                conversation_history=conversation_history,
                guidance=guidance,
                lead_context=lead_context,
                dynamic_examples=dynamic_examples,
            )

            messages = [
                {"role": "system", "content": prompt_module.SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            completion = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=500,
                temperature=0.7,
            )

            if not completion.choices:
                raise DeepSeekError("DeepSeek returned empty response")

            return completion.choices[0].message.content

        except DeepSeekError:
            raise
        except KeyError as e:
            raise DeepSeekError(f"No prompt available for stage {stage}: {e}") from e
        except Exception as e:
            raise DeepSeekError(f"DeepSeek API error: {e}") from e

    async def summarize_and_draft_comment(
        self,
        author_name: str,
        author_headline: str | None,
        author_category: str,
        post_snippet: str,
    ) -> tuple[str, str, int, int]:
        """Summarize a LinkedIn post and draft an engagement comment.

        Args:
            author_name: Name of the post author.
            author_headline: Author's LinkedIn headline.
            author_category: Category of the watched profile.
            post_snippet: The post content from search results.

        Returns:
            Tuple of (summary, draft_comment, prompt_tokens, completion_tokens).

        Raises:
            DeepSeekError: If the API call fails.
        """
        try:
            user_prompt = build_comment_drafter_prompt(
                author_name=author_name,
                author_headline=author_headline,
                author_category=author_category,
                post_snippet=post_snippet,
            )

            messages = [
                {"role": "system", "content": COMMENT_DRAFTER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            completion = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=500,
                temperature=0.7,
            )

            if not completion.choices:
                raise DeepSeekError("DeepSeek returned empty response for comment drafting")

            content = completion.choices[0].message.content

            # Extract token usage
            prompt_tokens = 0
            completion_tokens = 0
            if completion.usage:
                prompt_tokens = completion.usage.prompt_tokens or 0
                completion_tokens = completion.usage.completion_tokens or 0

            # Parse JSON response (strip markdown code fences if present)
            try:
                clean = content.strip()
                if clean.startswith("```"):
                    # Remove opening fence (e.g. ```json)
                    clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
                if clean.endswith("```"):
                    clean = clean[:-3]
                data = json.loads(clean.strip())
                summary = data.get("summary", "")
                comment = data.get("comment", "")
            except (json.JSONDecodeError, IndexError):
                # Fallback: use the raw content as both
                summary = "Could not parse summary"
                comment = content

            return summary, comment, prompt_tokens, completion_tokens

        except DeepSeekError:
            raise
        except Exception as e:
            raise DeepSeekError(f"DeepSeek comment drafting error: {e}") from e

    async def generate_draft(
        self,
        lead_name: str,
        lead_message: str,
        conversation_history: list[dict] | None = None,
        guidance: str | None = None,
        lead_context: dict | None = None,
        dynamic_examples: str = "",
    ) -> DraftResult:
        """Generate a draft reply using two-pass flow: detect stage, then generate.

        Args:
            lead_name: Name of the lead.
            lead_message: The lead's most recent message.
            conversation_history: Previous messages in the conversation.
            guidance: Optional user guidance for regeneration.
            lead_context: Optional lead context (company, title, etc.).
            dynamic_examples: Pre-formatted examples from similar past conversations.

        Returns:
            DraftResult with detected_stage, stage_reasoning, and reply.

        Raises:
            DeepSeekError: If the API call fails or returns empty response.
        """
        # Pass 1: Detect the funnel stage
        detected_stage, stage_reasoning = await self.detect_stage(
            lead_name=lead_name,
            lead_message=lead_message,
            conversation_history=conversation_history,
            lead_context=lead_context,
        )

        # Pass 2: Generate reply using stage-specific prompt
        reply = await self.generate_with_stage(
            lead_name=lead_name,
            lead_message=lead_message,
            stage=detected_stage,
            conversation_history=conversation_history,
            guidance=guidance,
            lead_context=lead_context,
            dynamic_examples=dynamic_examples,
        )

        return DraftResult(
            detected_stage=detected_stage,
            stage_reasoning=stage_reasoning,
            reply=reply,
        )


# Global client instance
_client: DeepSeekClient | None = None


def get_deepseek_client() -> DeepSeekClient:
    """Get or create the DeepSeek client singleton."""
    global _client
    if _client is None:
        _client = DeepSeekClient()
    return _client


async def generate_reply_draft(
    lead_name: str,
    lead_message: str,
    conversation_history: list[dict] | None = None,
    guidance: str | None = None,
    lead_context: dict | None = None,
    dynamic_examples: str = "",
) -> DraftResult:
    """Convenience function to generate a reply draft with stage detection.

    Args:
        lead_name: Name of the lead.
        lead_message: The lead's most recent message.
        conversation_history: Previous messages in the conversation.
        guidance: Optional user guidance for regeneration.
        lead_context: Optional lead context (company, title, etc.).
        dynamic_examples: Pre-formatted examples from similar past conversations.

    Returns:
        DraftResult with detected_stage, stage_reasoning, and reply.
    """
    client = get_deepseek_client()
    return await client.generate_draft(
        lead_name=lead_name,
        lead_message=lead_message,
        conversation_history=conversation_history,
        guidance=guidance,
        lead_context=lead_context,
        dynamic_examples=dynamic_examples,
    )


async def generate_reply_draft_with_judgment(
    lead_name: str,
    lead_message: str,
    conversation_history: list[dict] | None = None,
    lead_context: dict | None = None,
) -> DraftResult:
    """Generate a reply draft with Claude Sonnet judge loop.

    Flow: DeepSeek drafts -> Sonnet judges -> if score < 4.0, revise once -> return best.
    Falls back to unjudged draft if Anthropic API fails.

    Args:
        lead_name: Name of the lead.
        lead_message: The lead's most recent message.
        conversation_history: Previous messages in the conversation.
        lead_context: Optional lead context (company, title, etc.).

    Returns:
        DraftResult with judge_score, judge_feedback, and revision_count populated.
    """
    from app.services.judge import (
        JudgeError,
        MAX_REVISIONS,
        SCORE_THRESHOLD,
        judge_draft,
    )

    client = get_deepseek_client()

    # Step 1: Generate initial draft
    draft_result = await client.generate_draft(
        lead_name=lead_name,
        lead_message=lead_message,
        conversation_history=conversation_history,
        lead_context=lead_context,
    )

    # Step 2: Judge the draft
    try:
        judge_result = await judge_draft(
            lead_name=lead_name,
            lead_message=lead_message,
            ai_draft=draft_result.reply,
            conversation_history=conversation_history,
            lead_context=lead_context,
        )
    except JudgeError as e:
        logger.warning(f"Judge failed, returning unjudged draft: {e}")
        return draft_result

    best = draft_result
    best.judge_score = judge_result.weighted_score
    best.judge_feedback = judge_result.feedback

    # Step 3: Revise if below threshold
    if judge_result.weighted_score < SCORE_THRESHOLD and best.revision_count < MAX_REVISIONS:
        logger.info(
            f"Draft scored {judge_result.weighted_score:.2f} < {SCORE_THRESHOLD}, "
            f"revising with feedback: {judge_result.feedback[:100]}"
        )

        # Use judge feedback as guidance for revision
        revised_reply = await client.generate_with_stage(
            lead_name=lead_name,
            lead_message=lead_message,
            stage=draft_result.detected_stage,
            conversation_history=conversation_history,
            guidance=judge_result.feedback,
            lead_context=lead_context,
        )

        # Re-judge the revision
        try:
            revised_judge = await judge_draft(
                lead_name=lead_name,
                lead_message=lead_message,
                ai_draft=revised_reply,
                conversation_history=conversation_history,
                lead_context=lead_context,
            )

            # Keep whichever version scored higher
            if revised_judge.weighted_score >= judge_result.weighted_score:
                best.reply = revised_reply
                best.judge_score = revised_judge.weighted_score
                best.judge_feedback = revised_judge.feedback
            best.revision_count = 1

        except JudgeError as e:
            # Re-judge failed — keep revised reply but with original score
            logger.warning(f"Re-judge failed, keeping revised draft: {e}")
            best.reply = revised_reply
            best.revision_count = 1

    return best
