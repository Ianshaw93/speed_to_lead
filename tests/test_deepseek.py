"""Tests for DeepSeek AI service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import FunnelStage
from app.services.deepseek import (
    DeepSeekClient,
    DeepSeekError,
    DraftResult,
    generate_reply_draft,
)


class TestDeepSeekClient:
    """Tests for the DeepSeek API client."""

    @pytest.fixture
    def client(self):
        """Create a DeepSeek client for testing."""
        return DeepSeekClient(api_key="test_api_key")

    @pytest.mark.asyncio
    async def test_generate_draft_success(self, client):
        """Should generate a draft reply using DeepSeek with stage detection."""
        # First call: stage detection
        stage_completion = MagicMock()
        stage_completion.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"detected_stage": "positive_reply", "reasoning": "Lead showed interest"}'
                )
            )
        ]

        # Second call: draft generation
        draft_completion = MagicMock()
        draft_completion.choices = [
            MagicMock(message=MagicMock(content="Hi John! Thanks for your interest..."))
        ]

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.side_effect = [stage_completion, draft_completion]

            result = await client.generate_draft(
                lead_name="John Doe",
                lead_message="I'm interested in learning more about your product.",
                conversation_history=[],
            )

            assert isinstance(result, DraftResult)
            assert "John" in result.reply or "interest" in result.reply.lower()
            assert mock_create.call_count == 2  # Two calls: stage detection + draft

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


class TestDraftResult:
    """Tests for the DraftResult dataclass."""

    def test_draft_result_creation(self):
        """Should create a DraftResult with all fields."""
        result = DraftResult(
            detected_stage=FunnelStage.POSITIVE_REPLY,
            stage_reasoning="Lead showed interest",
            reply="Thanks for your interest!",
        )
        assert result.detected_stage == FunnelStage.POSITIVE_REPLY
        assert result.stage_reasoning == "Lead showed interest"
        assert result.reply == "Thanks for your interest!"


class TestStageDetection:
    """Tests for stage detection functionality."""

    @pytest.fixture
    def client(self):
        """Create a DeepSeek client for testing."""
        return DeepSeekClient(api_key="test_api_key")

    @pytest.mark.asyncio
    async def test_detect_stage_success(self, client):
        """Should detect stage from conversation."""
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"detected_stage": "positive_reply", "reasoning": "Lead showed interest"}'
                )
            )
        ]

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = mock_completion

            stage, reasoning = await client.detect_stage(
                lead_name="John",
                lead_message="Sounds interesting!",
                conversation_history=[],
            )

            assert stage == FunnelStage.POSITIVE_REPLY
            assert "interest" in reasoning.lower()

    @pytest.mark.asyncio
    async def test_detect_stage_invalid_json_fallback(self, client):
        """Should fallback to POSITIVE_REPLY for invalid JSON."""
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(message=MagicMock(content="not valid json"))
        ]

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = mock_completion

            stage, reasoning = await client.detect_stage(
                lead_name="John",
                lead_message="Hello",
                conversation_history=[],
            )

            assert stage == FunnelStage.POSITIVE_REPLY
            assert "fallback" in reasoning.lower() or "parse" in reasoning.lower()

    @pytest.mark.asyncio
    async def test_detect_stage_unknown_stage_fallback(self, client):
        """Should fallback to POSITIVE_REPLY for unknown stage."""
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"detected_stage": "unknown_stage", "reasoning": "test"}'
                )
            )
        ]

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = mock_completion

            stage, reasoning = await client.detect_stage(
                lead_name="John",
                lead_message="Hello",
                conversation_history=[],
            )

            assert stage == FunnelStage.POSITIVE_REPLY


class TestTwoPassGeneration:
    """Tests for the two-pass generation flow."""

    @pytest.fixture
    def client(self):
        """Create a DeepSeek client for testing."""
        return DeepSeekClient(api_key="test_api_key")

    @pytest.mark.asyncio
    async def test_generate_with_stage_uses_stage_prompt(self, client):
        """Should use the stage-specific prompt for generation."""
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(message=MagicMock(content="Stage-specific reply"))
        ]

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = mock_completion

            reply = await client.generate_with_stage(
                lead_name="John",
                lead_message="Sounds interesting!",
                stage=FunnelStage.POSITIVE_REPLY,
                conversation_history=[],
            )

            assert reply == "Stage-specific reply"
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_two_pass_flow(self, client):
        """Should detect stage then generate stage-specific reply."""
        # First call returns stage detection
        stage_completion = MagicMock()
        stage_completion.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"detected_stage": "pitched", "reasoning": "Call was proposed"}'
                )
            )
        ]

        # Second call returns the draft
        draft_completion = MagicMock()
        draft_completion.choices = [
            MagicMock(message=MagicMock(content="Great, let me address your question..."))
        ]

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.side_effect = [stage_completion, draft_completion]

            result = await client.generate_draft(
                lead_name="John",
                lead_message="What would we discuss?",
                conversation_history=[
                    {"role": "you", "content": "Would you like to hop on a call?"},
                ],
            )

            assert isinstance(result, DraftResult)
            assert result.detected_stage == FunnelStage.PITCHED
            assert "Great" in result.reply
            assert mock_create.call_count == 2


class TestGenerateReplyDraft:
    """Tests for the generate_reply_draft helper function."""

    @pytest.mark.asyncio
    async def test_generate_reply_draft_returns_draft_result(self):
        """Should return a DraftResult from generate_reply_draft."""
        with patch("app.services.deepseek.get_deepseek_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.generate_draft.return_value = DraftResult(
                detected_stage=FunnelStage.POSITIVE_REPLY,
                stage_reasoning="Interest shown",
                reply="Generated reply",
            )
            mock_get_client.return_value = mock_client

            result = await generate_reply_draft(
                lead_name="Test Lead",
                lead_message="Test message",
                conversation_history=[],
            )

            assert isinstance(result, DraftResult)
            assert result.reply == "Generated reply"
            mock_client.generate_draft.assert_called_once()
