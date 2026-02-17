"""Tests for pitched channel feature."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import (
    Conversation,
    FunnelStage,
    MessageDirection,
    MessageLog,
    Prospect,
    ProspectSource,
)
from app.services.slack import (
    build_pitched_card_blocks,
    build_pitched_card_buttons,
)


class TestPitchedCardBlocks:
    """Tests for pitched card block builders."""

    def test_build_basic_card(self):
        """Should build a card with header, status, and LinkedIn link."""
        blocks = build_pitched_card_blocks(
            lead_name="John Doe",
            lead_title="VP Engineering",
            lead_company="Acme Corp",
            linkedin_url="https://linkedin.com/in/johndoe",
            funnel_stage=FunnelStage.PITCHED,
        )

        assert len(blocks) >= 4  # header, context, section, divider
        assert blocks[0]["type"] == "header"
        assert "John Doe" in blocks[0]["text"]["text"]
        assert "VP Engineering" in blocks[0]["text"]["text"]
        assert "Acme Corp" in blocks[0]["text"]["text"]

        # Check status context
        assert blocks[1]["type"] == "context"
        status_text = blocks[1]["elements"][0]["text"]
        assert "Pitched" in status_text

        # Check LinkedIn link
        assert blocks[2]["type"] == "section"
        assert "linkedin.com/in/johndoe" in blocks[2]["text"]["text"]

    def test_build_card_no_title_company(self):
        """Should handle missing title and company."""
        blocks = build_pitched_card_blocks(
            lead_name="Jane Smith",
            lead_title=None,
            lead_company=None,
            linkedin_url="https://linkedin.com/in/janesmith",
            funnel_stage=FunnelStage.PITCHED,
        )

        assert blocks[0]["text"]["text"] == "Jane Smith"

    def test_build_card_with_messages(self):
        """Should include recent inbound messages."""
        messages = [
            {"content": "I'm interested in your service!"},
            {"content": "Can you tell me more about pricing?"},
        ]
        blocks = build_pitched_card_blocks(
            lead_name="Test Lead",
            lead_title=None,
            lead_company=None,
            linkedin_url="https://linkedin.com/in/test",
            funnel_stage=FunnelStage.PITCHED,
            recent_messages=messages,
        )

        block_text = str(blocks)
        assert "interested in your service" in block_text
        assert "pricing" in block_text

    def test_build_card_truncates_long_messages(self):
        """Should truncate messages longer than 200 characters."""
        long_msg = "x" * 250
        messages = [{"content": long_msg}]
        blocks = build_pitched_card_blocks(
            lead_name="Test Lead",
            lead_title=None,
            lead_company=None,
            linkedin_url="https://linkedin.com/in/test",
            funnel_stage=FunnelStage.PITCHED,
            recent_messages=messages,
        )

        block_text = str(blocks)
        # Should be truncated to 197 + "..."
        assert "..." in block_text
        assert long_msg not in block_text  # Full message shouldn't appear

    def test_build_card_calendar_sent_stage(self):
        """Should display calendar sent stage correctly."""
        blocks = build_pitched_card_blocks(
            lead_name="Test Lead",
            lead_title=None,
            lead_company=None,
            linkedin_url="https://linkedin.com/in/test",
            funnel_stage=FunnelStage.CALENDAR_SENT,
        )

        status_text = blocks[1]["elements"][0]["text"]
        assert "Calendar Sent" in status_text

    def test_build_card_truncates_long_header(self):
        """Should truncate header if name + title + company exceeds 148 chars."""
        blocks = build_pitched_card_blocks(
            lead_name="A" * 50,
            lead_title="B" * 50,
            lead_company="C" * 80,
            linkedin_url="https://linkedin.com/in/test",
            funnel_stage=FunnelStage.PITCHED,
        )

        header_text = blocks[0]["text"]["text"]
        assert len(header_text) <= 148


class TestPitchedCardButtons:
    """Tests for pitched card action buttons."""

    def test_build_buttons(self):
        """Should build correct action buttons."""
        prospect_id = uuid.uuid4()
        button_blocks = build_pitched_card_buttons(prospect_id)

        assert len(button_blocks) == 1
        elements = button_blocks[0]["elements"]
        assert len(elements) == 3

        action_ids = [el["action_id"] for el in elements]
        assert "pitched_send_message" in action_ids
        assert "pitched_calendar_sent" in action_ids
        assert "pitched_booked" in action_ids

    def test_buttons_contain_prospect_id(self):
        """Should include prospect ID in button values."""
        prospect_id = uuid.uuid4()
        button_blocks = build_pitched_card_buttons(prospect_id)

        for element in button_blocks[0]["elements"]:
            assert element["value"] == str(prospect_id)

    def test_send_message_is_primary(self):
        """Send Message button should have primary style."""
        prospect_id = uuid.uuid4()
        button_blocks = build_pitched_card_buttons(prospect_id)

        send_btn = next(
            el for el in button_blocks[0]["elements"]
            if el["action_id"] == "pitched_send_message"
        )
        assert send_btn["style"] == "primary"


class TestSlackBotPitchedMethods:
    """Tests for SlackBot pitched channel methods."""

    @pytest.mark.asyncio
    async def test_send_pitched_card(self):
        """Should post a card and return message_ts."""
        from app.services.slack import SlackBot

        bot = SlackBot(
            bot_token="xoxb-test",
            channel_id="C_MAIN",
            pitched_channel_id="C_PITCHED",
        )
        bot._client = AsyncMock()
        bot._client.chat_postMessage.return_value = {"ts": "1234567890.123456"}

        ts = await bot.send_pitched_card(
            prospect_id=uuid.uuid4(),
            lead_name="Test Lead",
            lead_title="CEO",
            lead_company="TestCo",
            linkedin_url="https://linkedin.com/in/test",
            funnel_stage=FunnelStage.PITCHED,
        )

        assert ts == "1234567890.123456"
        bot._client.chat_postMessage.assert_called_once()
        call_kwargs = bot._client.chat_postMessage.call_args
        assert call_kwargs.kwargs["channel"] == "C_PITCHED"

    @pytest.mark.asyncio
    async def test_update_pitched_card_booked_removes_buttons(self):
        """Should replace buttons with confirmation when stage is BOOKED."""
        from app.services.slack import SlackBot

        bot = SlackBot(
            bot_token="xoxb-test",
            channel_id="C_MAIN",
            pitched_channel_id="C_PITCHED",
        )
        bot._client = AsyncMock()

        await bot.update_pitched_card(
            message_ts="1234567890.123456",
            prospect_id=uuid.uuid4(),
            lead_name="Test Lead",
            lead_title=None,
            lead_company=None,
            linkedin_url="https://linkedin.com/in/test",
            funnel_stage=FunnelStage.BOOKED,
        )

        bot._client.chat_update.assert_called_once()
        call_kwargs = bot._client.chat_update.call_args
        blocks = call_kwargs.kwargs["blocks"]

        # Should have context block with "Meeting booked!" instead of actions
        has_booked_context = any(
            block.get("type") == "context"
            and any("Meeting booked!" in el.get("text", "") for el in block.get("elements", []))
            for block in blocks
        )
        assert has_booked_context

        # Should NOT have actions block
        has_actions = any(block.get("type") == "actions" for block in blocks)
        assert not has_actions

    @pytest.mark.asyncio
    async def test_update_pitched_card_calendar_sent_keeps_buttons(self):
        """Should keep buttons when stage is not BOOKED."""
        from app.services.slack import SlackBot

        bot = SlackBot(
            bot_token="xoxb-test",
            channel_id="C_MAIN",
            pitched_channel_id="C_PITCHED",
        )
        bot._client = AsyncMock()

        await bot.update_pitched_card(
            message_ts="1234567890.123456",
            prospect_id=uuid.uuid4(),
            lead_name="Test Lead",
            lead_title=None,
            lead_company=None,
            linkedin_url="https://linkedin.com/in/test",
            funnel_stage=FunnelStage.CALENDAR_SENT,
        )

        call_kwargs = bot._client.chat_update.call_args
        blocks = call_kwargs.kwargs["blocks"]

        has_actions = any(block.get("type") == "actions" for block in blocks)
        assert has_actions

    @pytest.mark.asyncio
    async def test_open_pitched_send_message_modal(self):
        """Should open a modal with message input and optional scheduler."""
        from app.services.slack import SlackBot

        bot = SlackBot(bot_token="xoxb-test", channel_id="C_MAIN")
        bot._client = AsyncMock()

        prospect_id = uuid.uuid4()
        await bot.open_pitched_send_message_modal(
            trigger_id="trigger123",
            prospect_id=prospect_id,
            lead_name="Test Lead",
        )

        bot._client.views_open.assert_called_once()
        call_kwargs = bot._client.views_open.call_args
        view = call_kwargs.kwargs["view"]

        assert view["callback_id"] == "pitched_send_message_submit"
        assert view["private_metadata"] == str(prospect_id)

        # Should have message input and schedule input
        block_ids = [b.get("block_id") for b in view["blocks"] if "block_id" in b]
        assert "message_input" in block_ids
        assert "schedule_input" in block_ids

    @pytest.mark.asyncio
    async def test_pitched_channel_fallback_to_main(self):
        """Should fall back to main channel if pitched channel not configured."""
        from app.services.slack import SlackBot

        bot = SlackBot(
            bot_token="xoxb-test",
            channel_id="C_MAIN",
            pitched_channel_id="",
        )
        # When pitched_channel_id is empty, constructor falls back to channel_id
        # But empty string is falsy, so it should fall back
        # The constructor uses `or` so empty string -> settings -> main channel
        assert bot._pitched_channel_id  # Should not be empty


class TestPitchedStageUpdateIntegration:
    """Integration tests for pitched stage updates via handlers."""

    @pytest.mark.asyncio
    async def test_process_pitched_stage_update_calendar_sent(self, test_db_session):
        """Should update prospect timestamps and conversation funnel stage."""
        # Create test data
        conversation = Conversation(
            heyreach_lead_id="test_lead_123",
            linkedin_profile_url="https://linkedin.com/in/test",
            lead_name="Test Lead",
            linkedin_account_id="account_123",
            funnel_stage=FunnelStage.PITCHED,
        )
        test_db_session.add(conversation)
        await test_db_session.flush()

        prospect = Prospect(
            linkedin_url="https://linkedin.com/in/test",
            full_name="Test Lead",
            job_title="CEO",
            company_name="TestCo",
            source_type=ProspectSource.COLD_OUTREACH,
            pitched_at=datetime.now(timezone.utc),
            conversation_id=conversation.id,
        )
        test_db_session.add(prospect)
        await test_db_session.commit()

        # Import the handler
        from app.routers.slack import _process_pitched_stage_update

        with patch("app.routers.slack.async_session_factory") as mock_factory, \
             patch("app.routers.slack.get_slack_bot") as mock_get_bot:

            mock_bot = AsyncMock()
            mock_get_bot.return_value = mock_bot
            mock_bot.send_pitched_card.return_value = "new_ts"

            # Use our test session
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            await _process_pitched_stage_update(
                prospect_id=prospect.id,
                message_ts="1234567890.123456",
                stage="calendar_sent",
            )

        # Verify prospect was updated
        await test_db_session.refresh(prospect)
        assert prospect.calendar_sent_at is not None
        assert prospect.pitched_at is not None

        # Verify conversation was updated
        await test_db_session.refresh(conversation)
        assert conversation.funnel_stage == FunnelStage.CALENDAR_SENT

    @pytest.mark.asyncio
    async def test_process_pitched_stage_update_booked(self, test_db_session):
        """Should set all timestamps when marking as booked."""
        conversation = Conversation(
            heyreach_lead_id="test_lead_123",
            linkedin_profile_url="https://linkedin.com/in/test",
            lead_name="Test Lead",
            linkedin_account_id="account_123",
            funnel_stage=FunnelStage.PITCHED,
        )
        test_db_session.add(conversation)
        await test_db_session.flush()

        prospect = Prospect(
            linkedin_url="https://linkedin.com/in/test",
            full_name="Test Lead",
            source_type=ProspectSource.COLD_OUTREACH,
            conversation_id=conversation.id,
        )
        test_db_session.add(prospect)
        await test_db_session.commit()

        from app.routers.slack import _process_pitched_stage_update

        with patch("app.routers.slack.async_session_factory") as mock_factory, \
             patch("app.routers.slack.get_slack_bot") as mock_get_bot:

            mock_bot = AsyncMock()
            mock_get_bot.return_value = mock_bot
            mock_bot.send_pitched_card.return_value = "new_ts"

            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            await _process_pitched_stage_update(
                prospect_id=prospect.id,
                message_ts="1234567890.123456",
                stage="booked",
            )

        await test_db_session.refresh(prospect)
        assert prospect.pitched_at is not None
        assert prospect.calendar_sent_at is not None
        assert prospect.booked_at is not None


class TestGetRecentInboundMessages:
    """Tests for _get_recent_inbound_messages helper."""

    @pytest.mark.asyncio
    async def test_returns_inbound_messages(self, test_db_session):
        """Should return last 3 inbound messages."""
        conversation = Conversation(
            heyreach_lead_id="test_lead",
            linkedin_profile_url="https://linkedin.com/in/test",
            lead_name="Test Lead",
        )
        test_db_session.add(conversation)
        await test_db_session.flush()

        # Add 4 messages (should only return 3)
        for i in range(4):
            msg = MessageLog(
                conversation_id=conversation.id,
                direction=MessageDirection.INBOUND,
                content=f"Message {i}",
            )
            test_db_session.add(msg)

        # Add 1 outbound (should not be returned)
        outbound = MessageLog(
            conversation_id=conversation.id,
            direction=MessageDirection.OUTBOUND,
            content="Outbound message",
        )
        test_db_session.add(outbound)
        await test_db_session.commit()

        prospect = Prospect(
            linkedin_url="https://linkedin.com/in/test",
            full_name="Test Lead",
            source_type=ProspectSource.COLD_OUTREACH,
            conversation_id=conversation.id,
        )
        test_db_session.add(prospect)
        await test_db_session.commit()

        from app.routers.slack import _get_recent_inbound_messages

        messages = await _get_recent_inbound_messages(test_db_session, prospect)

        assert len(messages) == 3
        assert all("content" in m for m in messages)
        # Should not contain outbound
        contents = [m["content"] for m in messages]
        assert "Outbound message" not in contents

    @pytest.mark.asyncio
    async def test_returns_empty_without_conversation(self, test_db_session):
        """Should return empty list if prospect has no conversation."""
        prospect = Prospect(
            linkedin_url="https://linkedin.com/in/test",
            full_name="Test Lead",
            source_type=ProspectSource.COLD_OUTREACH,
        )
        test_db_session.add(prospect)
        await test_db_session.commit()

        from app.routers.slack import _get_recent_inbound_messages

        messages = await _get_recent_inbound_messages(test_db_session, prospect)
        assert messages == []


class TestScheduledMessage:
    """Tests for scheduled message functionality."""

    @pytest.mark.asyncio
    async def test_add_scheduled_message(self):
        """Should add a job to the scheduler."""
        from app.services.scheduler import SchedulerService

        scheduler = SchedulerService()
        scheduler._scheduler.start()

        try:
            prospect_id = uuid.uuid4()
            run_time = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

            job_id = scheduler.add_scheduled_message(
                prospect_id=prospect_id,
                message_text="Hello!",
                run_time=run_time,
            )

            assert job_id is not None
            job = scheduler._scheduler.get_job(job_id)
            assert job is not None
        finally:
            scheduler.shutdown(wait=False)
