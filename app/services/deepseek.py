"""DeepSeek AI client for generating LinkedIn reply drafts."""

from openai import AsyncOpenAI

from app.config import settings
from app.prompts.sales_assistant import SYSTEM_PROMPT, build_user_prompt


class DeepSeekError(Exception):
    """Custom exception for DeepSeek API errors."""

    pass


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

    async def generate_draft(
        self,
        lead_name: str,
        lead_message: str,
        conversation_history: list[dict] | None = None,
        guidance: str | None = None,
    ) -> str:
        """Generate a draft reply using DeepSeek.

        Args:
            lead_name: Name of the lead.
            lead_message: The lead's most recent message.
            conversation_history: Previous messages in the conversation.
            guidance: Optional user guidance for regeneration.

        Returns:
            The generated draft reply text.

        Raises:
            DeepSeekError: If the API call fails or returns empty response.
        """
        try:
            user_prompt = build_user_prompt(
                lead_name=lead_name,
                lead_message=lead_message,
                conversation_history=conversation_history,
                guidance=guidance,
            )

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
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
        except Exception as e:
            raise DeepSeekError(f"DeepSeek API error: {e}") from e


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
) -> str:
    """Convenience function to generate a reply draft.

    Args:
        lead_name: Name of the lead.
        lead_message: The lead's most recent message.
        conversation_history: Previous messages in the conversation.
        guidance: Optional user guidance for regeneration.

    Returns:
        The generated draft reply text.
    """
    client = get_deepseek_client()
    return await client.generate_draft(
        lead_name=lead_name,
        lead_message=lead_message,
        conversation_history=conversation_history,
        guidance=guidance,
    )
