"""Integration tests for the complete webhook-to-Slack flow."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Conversation, Draft, DraftStatus, MessageDirection, MessageLog


class TestWebhookToSlackFlow:
    """Integration tests for the HeyReach webhook to Slack notification flow."""

    @pytest.fixture
    def heyreach_payload(self):
        """Create a valid HeyReach webhook payload."""
        return {
            "is_inmail": False,
            "recent_messages": [
                {
                    "creation_time": "2026-01-28T10:00:00Z",
                    "message": "Hi, I saw your profile and I'm interested in learning more about your product!",
                    "is_reply": True,
                }
            ],
            "conversation_id": "test-conv-12345",
            "campaign": {"name": "LinkedIn Outreach Q1", "id": 456},
            "sender": {
                "id": 789,
                "first_name": "Sales",
                "full_name": "Sales Rep",
            },
            "lead": {
                "id": "lead-abc-123",
                "profile_url": "https://www.linkedin.com/in/johndoe",
                "full_name": "John Doe",
                "company_name": "Acme Corporation",
                "position": "VP of Engineering",
            },
            "timestamp": "2026-01-28T10:00:00Z",
            "event_type": "every_message_reply_received",
        }

    @pytest_asyncio.fixture
    async def integration_db_engine(self):
        """Create a test database engine for integration tests."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=False,
        )

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        yield engine

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        await engine.dispose()

    @pytest_asyncio.fixture
    async def integration_session_factory(self, integration_db_engine):
        """Create a session factory for integration tests."""
        return async_sessionmaker(
            integration_db_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @pytest.mark.asyncio
    async def test_full_webhook_to_slack_flow(
        self, test_client: AsyncClient, heyreach_payload
    ):
        """Test the complete flow from webhook receipt to Slack notification.

        This test verifies that:
        1. Webhook receives and parses HeyReach payload
        2. AI draft is generated via DeepSeek
        3. Slack notification is sent with the draft
        4. Draft is stored in database with pending status
        """
        mock_slack_ts = "1706436000.123456"
        mock_ai_draft = "Hi John! Thanks for reaching out. I'd love to tell you more about our product. Would you be available for a quick call this week?"

        with (
            patch("app.main.generate_reply_draft", new_callable=AsyncMock) as mock_deepseek,
            patch("app.main.SlackBot") as MockSlackBot,
            patch("app.main.async_session_factory") as mock_session_factory,
        ):
            # Setup mocks
            mock_deepseek.return_value = mock_ai_draft

            mock_slack_bot = MagicMock()
            mock_slack_bot.send_draft_notification = AsyncMock(return_value=mock_slack_ts)
            MockSlackBot.return_value = mock_slack_bot

            # Create in-memory database for the test
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            session_factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            mock_session_factory.return_value = session_factory()

            # Send webhook request
            response = await test_client.post("/webhook/heyreach", json=heyreach_payload)

            # Verify webhook response
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "received"
            assert data["conversation_id"] == "test-conv-12345"
            assert data["lead_name"] == "John Doe"

            # Wait for background task to complete (in tests, we need to give it time)
            import asyncio
            await asyncio.sleep(0.5)

            # Verify DeepSeek was called with correct parameters
            mock_deepseek.assert_called_once()
            call_kwargs = mock_deepseek.call_args.kwargs
            assert call_kwargs["lead_name"] == "John Doe"
            assert "interested in learning more" in call_kwargs["lead_message"]

            # Verify Slack notification was sent
            mock_slack_bot.send_draft_notification.assert_called_once()
            slack_call_kwargs = mock_slack_bot.send_draft_notification.call_args.kwargs
            assert slack_call_kwargs["lead_name"] == "John Doe"
            assert slack_call_kwargs["lead_company"] == "Acme Corporation"
            assert slack_call_kwargs["ai_draft"] == mock_ai_draft
            assert "interested in learning more" in slack_call_kwargs["lead_message"]

            await engine.dispose()

    @pytest.mark.asyncio
    async def test_webhook_creates_conversation_and_draft(
        self, test_client: AsyncClient, heyreach_payload
    ):
        """Test that webhook creates conversation and draft records in database."""
        mock_slack_ts = "1706436000.789012"
        mock_ai_draft = "Thanks for your interest!"

        # Create a shared session to verify database records
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        with (
            patch("app.main.generate_reply_draft", new_callable=AsyncMock) as mock_deepseek,
            patch("app.main.SlackBot") as MockSlackBot,
            patch("app.main.async_session_factory") as mock_session_factory,
        ):
            mock_deepseek.return_value = mock_ai_draft

            mock_slack_bot = MagicMock()
            mock_slack_bot.send_draft_notification = AsyncMock(return_value=mock_slack_ts)
            MockSlackBot.return_value = mock_slack_bot

            mock_session_factory.return_value = session_factory()

            # Send webhook
            response = await test_client.post("/webhook/heyreach", json=heyreach_payload)
            assert response.status_code == 200

            # Wait for background processing
            import asyncio
            await asyncio.sleep(0.5)

            # Verify database records
            async with session_factory() as session:
                # Check conversation was created
                result = await session.execute(
                    select(Conversation).where(
                        Conversation.heyreach_lead_id == "test-conv-12345"
                    )
                )
                conversation = result.scalar_one_or_none()
                assert conversation is not None
                assert conversation.lead_name == "John Doe"

                # Check message log was created
                result = await session.execute(
                    select(MessageLog).where(
                        MessageLog.conversation_id == conversation.id
                    )
                )
                message_log = result.scalar_one_or_none()
                assert message_log is not None
                assert message_log.direction == MessageDirection.INBOUND
                assert "interested in learning more" in message_log.content

                # Check draft was created
                result = await session.execute(
                    select(Draft).where(Draft.conversation_id == conversation.id)
                )
                draft = result.scalar_one_or_none()
                assert draft is not None
                assert draft.status == DraftStatus.PENDING
                assert draft.ai_draft == mock_ai_draft
                assert draft.slack_message_ts == mock_slack_ts

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_webhook_updates_existing_conversation(
        self, test_client: AsyncClient, heyreach_payload
    ):
        """Test that subsequent webhooks update existing conversation."""
        mock_ai_draft = "Follow up response!"
        mock_slack_ts = "1706436000.111111"

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        # Pre-create a conversation
        async with session_factory() as session:
            existing_conversation = Conversation(
                heyreach_lead_id="test-conv-12345",
                linkedin_profile_url="https://linkedin.com/in/johndoe",
                lead_name="John Doe",
                conversation_history=[{"role": "lead", "content": "Previous message"}],
            )
            session.add(existing_conversation)
            await session.commit()
            existing_id = existing_conversation.id

        with (
            patch("app.main.generate_reply_draft", new_callable=AsyncMock) as mock_deepseek,
            patch("app.main.SlackBot") as MockSlackBot,
            patch("app.main.async_session_factory") as mock_session_factory,
        ):
            mock_deepseek.return_value = mock_ai_draft

            mock_slack_bot = MagicMock()
            mock_slack_bot.send_draft_notification = AsyncMock(return_value=mock_slack_ts)
            MockSlackBot.return_value = mock_slack_bot

            mock_session_factory.return_value = session_factory()

            # Send new webhook for same conversation
            response = await test_client.post("/webhook/heyreach", json=heyreach_payload)
            assert response.status_code == 200

            import asyncio
            await asyncio.sleep(0.5)

            # Verify conversation was updated, not duplicated
            async with session_factory() as session:
                result = await session.execute(
                    select(Conversation).where(
                        Conversation.heyreach_lead_id == "test-conv-12345"
                    )
                )
                conversations = result.scalars().all()
                assert len(conversations) == 1

                conversation = conversations[0]
                assert conversation.id == existing_id
                # Conversation history should be updated
                assert len(conversation.conversation_history) >= 1

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_slack_notification_contains_lead_info(
        self, test_client: AsyncClient, heyreach_payload
    ):
        """Test that Slack notification includes all lead information."""
        mock_slack_ts = "1706436000.222222"
        mock_ai_draft = "Generated AI response"

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        with (
            patch("app.main.generate_reply_draft", new_callable=AsyncMock) as mock_deepseek,
            patch("app.main.SlackBot") as MockSlackBot,
            patch("app.main.async_session_factory") as mock_session_factory,
        ):
            mock_deepseek.return_value = mock_ai_draft

            mock_slack_bot = MagicMock()
            mock_slack_bot.send_draft_notification = AsyncMock(return_value=mock_slack_ts)
            MockSlackBot.return_value = mock_slack_bot

            mock_session_factory.return_value = session_factory()

            response = await test_client.post("/webhook/heyreach", json=heyreach_payload)
            assert response.status_code == 200

            import asyncio
            await asyncio.sleep(0.5)

            # Verify Slack notification parameters
            mock_slack_bot.send_draft_notification.assert_called_once()
            call_kwargs = mock_slack_bot.send_draft_notification.call_args.kwargs

            # Check all lead info is passed to Slack
            assert call_kwargs["lead_name"] == "John Doe"
            assert call_kwargs["lead_company"] == "Acme Corporation"
            assert call_kwargs["ai_draft"] == mock_ai_draft
            assert "test-conv-12345" in call_kwargs["linkedin_url"]
            assert call_kwargs["lead_message"] == heyreach_payload["recent_messages"][0]["message"]

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_deepseek_receives_conversation_context(
        self, test_client: AsyncClient, heyreach_payload
    ):
        """Test that DeepSeek AI receives proper conversation context."""
        mock_slack_ts = "1706436000.333333"
        mock_ai_draft = "Contextual response"

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        with (
            patch("app.main.generate_reply_draft", new_callable=AsyncMock) as mock_deepseek,
            patch("app.main.SlackBot") as MockSlackBot,
            patch("app.main.async_session_factory") as mock_session_factory,
        ):
            mock_deepseek.return_value = mock_ai_draft

            mock_slack_bot = MagicMock()
            mock_slack_bot.send_draft_notification = AsyncMock(return_value=mock_slack_ts)
            MockSlackBot.return_value = mock_slack_bot

            mock_session_factory.return_value = session_factory()

            response = await test_client.post("/webhook/heyreach", json=heyreach_payload)
            assert response.status_code == 200

            import asyncio
            await asyncio.sleep(0.5)

            # Verify DeepSeek received proper context
            mock_deepseek.assert_called_once()
            call_kwargs = mock_deepseek.call_args.kwargs

            assert call_kwargs["lead_name"] == "John Doe"
            assert call_kwargs["lead_message"] == heyreach_payload["recent_messages"][0]["message"]
            assert "conversation_history" in call_kwargs
            assert isinstance(call_kwargs["conversation_history"], list)

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_error_handling_when_deepseek_fails(
        self, test_client: AsyncClient, heyreach_payload
    ):
        """Test graceful handling when DeepSeek API fails."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        with (
            patch("app.main.generate_reply_draft", new_callable=AsyncMock) as mock_deepseek,
            patch("app.main.SlackBot") as MockSlackBot,
            patch("app.main.async_session_factory") as mock_session_factory,
        ):
            # Simulate DeepSeek failure
            mock_deepseek.side_effect = Exception("DeepSeek API error")

            mock_slack_bot = MagicMock()
            mock_slack_bot.send_draft_notification = AsyncMock()
            MockSlackBot.return_value = mock_slack_bot

            mock_session_factory.return_value = session_factory()

            # Webhook should still return 200 (background task handles error)
            response = await test_client.post("/webhook/heyreach", json=heyreach_payload)
            assert response.status_code == 200

            import asyncio
            await asyncio.sleep(0.5)

            # Slack should NOT be called if DeepSeek failed
            mock_slack_bot.send_draft_notification.assert_not_called()

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_error_handling_when_slack_fails(
        self, test_client: AsyncClient, heyreach_payload
    ):
        """Test graceful handling when Slack API fails."""
        mock_ai_draft = "Generated draft"

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        with (
            patch("app.main.generate_reply_draft", new_callable=AsyncMock) as mock_deepseek,
            patch("app.main.SlackBot") as MockSlackBot,
            patch("app.main.async_session_factory") as mock_session_factory,
        ):
            mock_deepseek.return_value = mock_ai_draft

            mock_slack_bot = MagicMock()
            mock_slack_bot.send_draft_notification = AsyncMock(
                side_effect=Exception("Slack API error")
            )
            MockSlackBot.return_value = mock_slack_bot

            mock_session_factory.return_value = session_factory()

            # Webhook should still return 200 (background task handles error)
            response = await test_client.post("/webhook/heyreach", json=heyreach_payload)
            assert response.status_code == 200

            import asyncio
            await asyncio.sleep(0.5)

            # DeepSeek should have been called
            mock_deepseek.assert_called_once()
            # Slack was attempted
            mock_slack_bot.send_draft_notification.assert_called_once()

            # Draft should NOT be saved due to Slack failure
            async with session_factory() as session:
                result = await session.execute(select(Draft))
                drafts = result.scalars().all()
                assert len(drafts) == 0

        await engine.dispose()
import pytest_asyncio
