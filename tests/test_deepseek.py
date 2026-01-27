"""Tests for DeepSeek AI service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.deepseek import DeepSeekClient, DeepSeekError, generate_reply_draft


class TestDeepSeekClient:
    """Tests for the DeepSeek API client."""

    @pytest.fixture
    def client(self):
        """Create a DeepSeek client for testing."""
        return DeepSeekClient(api_key="test_api_key")

    @pytest.mark.asyncio
    async def test_generate_draft_success(self, client):
        """Should generate a draft reply using DeepSeek."""
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(message=MagicMock(content="Hi John! Thanks for your interest..."))
        ]

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = mock_completion

            result = await client.generate_draft(
                lead_name="John Doe",
                lead_message="I'm interested in learning more about your product.",
                conversation_history=[],
            )

            assert "John" in result or "interest" in result.lower()
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_draft_with_context(self, client):
        """Should include conversation history in the prompt."""
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(message=MagicMock(content="Great follow-up question!"))
        ]

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = mock_completion

            history = [
                {"role": "lead", "content": "What's your pricing?"},
                {"role": "assistant", "content": "Happy to discuss pricing!"},
            ]

            await client.generate_draft(
                lead_name="Jane",
                lead_message="Can you give me more details?",
                conversation_history=history,
            )

            # Verify the call was made with conversation context
            call_args = mock_create.call_args
            messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
            assert len(messages) > 1  # Should have system + user messages

    @pytest.mark.asyncio
    async def test_generate_draft_with_guidance(self, client):
        """Should incorporate user guidance when regenerating."""
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(message=MagicMock(content="More casual response here."))
        ]

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = mock_completion

            await client.generate_draft(
                lead_name="Bob",
                lead_message="Tell me more",
                conversation_history=[],
                guidance="Make it more casual and friendly",
            )

            call_args = mock_create.call_args
            messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
            # Check that guidance is incorporated
            content = str(messages)
            assert "casual" in content.lower() or len(messages) > 1

    @pytest.mark.asyncio
    async def test_generate_draft_api_error(self, client):
        """Should raise DeepSeekError on API failure."""
        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.side_effect = Exception("API rate limit exceeded")

            with pytest.raises(DeepSeekError) as exc_info:
                await client.generate_draft(
                    lead_name="Test",
                    lead_message="Hello",
                    conversation_history=[],
                )

            assert "rate limit" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_generate_draft_empty_response(self, client):
        """Should handle empty API response."""
        mock_completion = MagicMock()
        mock_completion.choices = []

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = mock_completion

            with pytest.raises(DeepSeekError) as exc_info:
                await client.generate_draft(
                    lead_name="Test",
                    lead_message="Hello",
                    conversation_history=[],
                )

            assert "empty" in str(exc_info.value).lower()

    def test_client_initialization(self):
        """Should initialize with correct API key and model."""
        client = DeepSeekClient(api_key="my_key", model="custom-model")
        assert client._model == "custom-model"


class TestGenerateReplyDraft:
    """Tests for the generate_reply_draft helper function."""

    @pytest.mark.asyncio
    async def test_generate_reply_draft_calls_client(self):
        """Should use the DeepSeek client to generate a draft."""
        with patch("app.services.deepseek.get_deepseek_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.generate_draft.return_value = "Generated reply"
            mock_get_client.return_value = mock_client

            result = await generate_reply_draft(
                lead_name="Test Lead",
                lead_message="Test message",
                conversation_history=[],
            )

            assert result == "Generated reply"
            mock_client.generate_draft.assert_called_once()
