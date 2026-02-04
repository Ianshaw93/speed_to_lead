"""DeepSeek AI client for generating LinkedIn reply drafts with stage detection."""

import json
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.config import settings
from app.models import FunnelStage
from app.prompts.stage_detector import (
    STAGE_DETECTION_SYSTEM_PROMPT,
    build_stage_detection_prompt,
)
from app.prompts.stages import get_stage_prompt


class DeepSeekError(Exception):
    """Custom exception for DeepSeek API errors."""

    pass


@dataclass
class DraftResult:
    """Result from draft generation including detected stage."""

    detected_stage: FunnelStage
    stage_reasoning: str
    reply: str


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
    ) -> tuple[FunnelStage, str]:
        """Detect the funnel stage from conversation.

        Args:
            lead_name: Name of the lead.
            lead_message: The lead's most recent message.
            conversation_history: Previous messages in the conversation.

        Returns:
            Tuple of (FunnelStage, reasoning string).
        """
        try:
            user_prompt = build_stage_detection_prompt(
                lead_name=lead_name,
                lead_message=lead_message,
                conversation_history=conversation_history,
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
            data = json.loads(content)
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
            return FunnelStage.POSITIVE_REPLY, "Fallback: could not parse JSON response"

    async def generate_with_stage(
        self,
        lead_name: str,
        lead_message: str,
        stage: FunnelStage,
        conversation_history: list[dict] | None = None,
        guidance: str | None = None,
    ) -> str:
        """Generate a reply using stage-specific prompt.

        Args:
            lead_name: Name of the lead.
            lead_message: The lead's most recent message.
            stage: The detected funnel stage.
            conversation_history: Previous messages in the conversation.
            guidance: Optional user guidance for regeneration.

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

    async def generate_draft(
        self,
        lead_name: str,
        lead_message: str,
        conversation_history: list[dict] | None = None,
        guidance: str | None = None,
    ) -> DraftResult:
        """Generate a draft reply using two-pass flow: detect stage, then generate.

        Args:
            lead_name: Name of the lead.
            lead_message: The lead's most recent message.
            conversation_history: Previous messages in the conversation.
            guidance: Optional user guidance for regeneration.

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
        )

        # Pass 2: Generate reply using stage-specific prompt
        reply = await self.generate_with_stage(
            lead_name=lead_name,
            lead_message=lead_message,
            stage=detected_stage,
            conversation_history=conversation_history,
            guidance=guidance,
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
) -> DraftResult:
    """Convenience function to generate a reply draft with stage detection.

    Args:
        lead_name: Name of the lead.
        lead_message: The lead's most recent message.
        conversation_history: Previous messages in the conversation.
        guidance: Optional user guidance for regeneration.

    Returns:
        DraftResult with detected_stage, stage_reasoning, and reply.
    """
    client = get_deepseek_client()
    return await client.generate_draft(
        lead_name=lead_name,
        lead_message=lead_message,
        conversation_history=conversation_history,
        guidance=guidance,
    )
