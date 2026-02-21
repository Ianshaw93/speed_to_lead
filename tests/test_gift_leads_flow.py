"""Tests for the streamlined Gift Leads flow.

Tests the 3-click flow: Confirm ICP -> Review Leads -> Send DM.
"""

import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import FunnelStage, ProspectSource
from app.services.slack import build_classification_buttons


# =============================================================================
# ICP Extraction Helpers
# =============================================================================


class TestExtractIcpNicheFromDm:
    """Tests for extracting ICP niche from personalized DM text."""

    def test_extracts_from_target_pattern(self):
        from app.routers.slack import extract_icp_niche_from_dm

        msg = "Hey! You guys target naturopath clinic owners right? I came across your profile and thought you'd be a great fit."
        assert "naturopath clinic owners" in extract_icp_niche_from_dm(msg)

    def test_extracts_from_work_with_pattern(self):
        from app.routers.slack import extract_icp_niche_from_dm

        msg = "I see you work with wellness practitioners - that's awesome!"
        assert "wellness practitioners" in extract_icp_niche_from_dm(msg)

    def test_returns_empty_for_no_match(self):
        from app.routers.slack import extract_icp_niche_from_dm

        msg = "Hey, how are you doing today?"
        assert extract_icp_niche_from_dm(msg) == ""

    def test_handles_none_input(self):
        from app.routers.slack import extract_icp_niche_from_dm

        assert extract_icp_niche_from_dm(None) == ""

    def test_handles_empty_string(self):
        from app.routers.slack import extract_icp_niche_from_dm

        assert extract_icp_niche_from_dm("") == ""


class TestDeriveKeywordsFromIcp:
    """Tests for deriving search keywords from ICP description."""

    def test_basic_derivation(self):
        from app.routers.slack import derive_keywords_from_icp

        result = derive_keywords_from_icp("naturopath clinic owners")
        assert "naturopath" in result
        assert "clinic" in result

    def test_filters_stop_words(self):
        from app.routers.slack import derive_keywords_from_icp

        result = derive_keywords_from_icp("owners of dental clinics in the area")
        keywords = [k.strip() for k in result.split(",")]
        assert "of" not in keywords
        assert "in" not in keywords
        assert "the" not in keywords

    def test_handles_empty_input(self):
        from app.routers.slack import derive_keywords_from_icp

        assert derive_keywords_from_icp("") == ""

    def test_handles_none_input(self):
        from app.routers.slack import derive_keywords_from_icp

        assert derive_keywords_from_icp(None) == ""


# =============================================================================
# Button Presence Tests
# =============================================================================


class TestConfirmIcpButtonPresence:
    """Tests that confirm_icp_gift_leads button appears on every notification."""

    def test_button_present_with_prospect_id(self):
        """When prospect_id is provided, button should use confirm_icp_gift_leads action."""
        prospect_id = uuid.uuid4()
        draft_id = uuid.uuid4()
        blocks = build_classification_buttons(
            draft_id=draft_id,
            is_first_reply=False,
            prospect_id=prospect_id,
        )
        actions = blocks[0]["elements"]
        gift_btn = [a for a in actions if a["action_id"] == "confirm_icp_gift_leads"]
        assert len(gift_btn) == 1
        assert gift_btn[0]["value"] == str(prospect_id)

    def test_button_falls_back_to_gift_leads_without_prospect(self):
        """When prospect_id is None, button should use old gift_leads action with draft_id."""
        draft_id = uuid.uuid4()
        blocks = build_classification_buttons(
            draft_id=draft_id,
            is_first_reply=False,
            prospect_id=None,
        )
        actions = blocks[0]["elements"]
        gift_btn = [a for a in actions if a["action_id"] == "gift_leads"]
        assert len(gift_btn) == 1
        assert gift_btn[0]["value"] == str(draft_id)

    def test_button_present_on_first_reply(self):
        """Gift leads button always present, including on first replies."""
        prospect_id = uuid.uuid4()
        draft_id = uuid.uuid4()
        blocks = build_classification_buttons(
            draft_id=draft_id,
            is_first_reply=True,
            prospect_id=prospect_id,
        )
        actions = blocks[0]["elements"]
        gift_actions = [a["action_id"] for a in actions]
        assert "confirm_icp_gift_leads" in gift_actions


# =============================================================================
# ICP Pre-fill Tests
# =============================================================================


class TestConfirmIcpPreFill:
    """Tests that ICP is pre-filled for buying signal prospects and blank for others."""

    @pytest.mark.asyncio
    @patch("app.routers.slack.async_session_factory")
    @patch("app.routers.slack.get_slack_bot")
    async def test_prefills_for_buying_signal(self, mock_get_bot, mock_session_factory):
        """Buying signal prospect should have ICP pre-filled from personalized_message."""
        from app.routers.slack import handle_confirm_icp_gift_leads

        prospect_id = uuid.uuid4()

        # Create mock prospect
        mock_prospect = MagicMock()
        mock_prospect.id = prospect_id
        mock_prospect.full_name = "John Doe"
        mock_prospect.source_type = ProspectSource.BUYING_SIGNAL
        mock_prospect.personalized_message = "You guys target naturopath clinic owners right?"
        mock_prospect.icp_reason = "Targets health practitioners"
        mock_prospect.conversation_id = uuid.uuid4()

        # Create mock conversation
        mock_conversation = MagicMock()
        mock_conversation.conversation_history = [
            {"role": "lead", "content": "Yes we do target those!"}
        ]

        # Mock DB session
        mock_session = AsyncMock()
        # First call returns prospect, second returns conversation
        mock_result1 = MagicMock()
        mock_result1.scalar_one_or_none.return_value = mock_prospect
        mock_result2 = MagicMock()
        mock_result2.scalar_one_or_none.return_value = mock_conversation
        mock_session.execute = AsyncMock(side_effect=[mock_result1, mock_result2])

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory.return_value = mock_ctx

        mock_bot = AsyncMock()
        mock_get_bot.return_value = mock_bot

        await handle_confirm_icp_gift_leads(prospect_id, "trigger_123")

        # Verify modal was opened with pre-filled ICP
        mock_bot.open_confirm_icp_gift_leads_modal.assert_called_once()
        call_kwargs = mock_bot.open_confirm_icp_gift_leads_modal.call_args[1]
        assert call_kwargs["prefill_icp"] != ""
        assert call_kwargs["prefill_keywords"] != ""

    @pytest.mark.asyncio
    @patch("app.routers.slack.async_session_factory")
    @patch("app.routers.slack.get_slack_bot")
    async def test_blank_for_other_sources(self, mock_get_bot, mock_session_factory):
        """Non-buying-signal prospect should have blank ICP fields."""
        from app.routers.slack import handle_confirm_icp_gift_leads

        prospect_id = uuid.uuid4()

        mock_prospect = MagicMock()
        mock_prospect.id = prospect_id
        mock_prospect.full_name = "Jane Smith"
        mock_prospect.source_type = ProspectSource.COMPETITOR_POST
        mock_prospect.personalized_message = "Generic intro message"
        mock_prospect.icp_reason = None
        mock_prospect.conversation_id = uuid.uuid4()

        mock_conversation = MagicMock()
        mock_conversation.conversation_history = [
            {"role": "lead", "content": "Hey thanks for connecting!"}
        ]

        mock_session = AsyncMock()
        mock_result1 = MagicMock()
        mock_result1.scalar_one_or_none.return_value = mock_prospect
        mock_result2 = MagicMock()
        mock_result2.scalar_one_or_none.return_value = mock_conversation
        mock_session.execute = AsyncMock(side_effect=[mock_result1, mock_result2])

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory.return_value = mock_ctx

        mock_bot = AsyncMock()
        mock_get_bot.return_value = mock_bot

        await handle_confirm_icp_gift_leads(prospect_id, "trigger_123")

        mock_bot.open_confirm_icp_gift_leads_modal.assert_called_once()
        call_kwargs = mock_bot.open_confirm_icp_gift_leads_modal.call_args[1]
        assert call_kwargs["prefill_icp"] == ""
        assert call_kwargs["prefill_keywords"] == ""


# =============================================================================
# Results with Send Button Test
# =============================================================================


class TestGiftLeadsResultsWithSendButton:
    """Tests that search results include a Send Leads button."""

    @pytest.mark.asyncio
    async def test_results_include_send_button(self):
        """send_gift_leads_results_with_send_button should include a Send button."""
        from app.services.slack import SlackBot

        prospect_id = uuid.uuid4()
        leads = [
            {
                "full_name": "Lead One",
                "job_title": "CEO",
                "company_name": "Acme",
                "location": "USA",
                "activity_score": 85.0,
                "linkedin_url": "https://linkedin.com/in/leadone",
            }
        ]

        bot = SlackBot()
        with patch.object(bot, "_client") as mock_client:
            mock_client.chat_postMessage = AsyncMock(return_value={"ts": "123.456"})
            mock_client.files_upload_v2 = AsyncMock()

            ts = await bot.send_gift_leads_results_with_send_button(
                prospect_id=prospect_id,
                prospect_name="John Doe",
                leads=leads,
                pool_size=100,
                keywords=["ceo"],
            )

            # Check the blocks include a send button
            call_kwargs = mock_client.chat_postMessage.call_args[1]
            blocks = call_kwargs["blocks"]
            actions_blocks = [b for b in blocks if b.get("type") == "actions"]
            assert len(actions_blocks) == 1
            send_btn = actions_blocks[0]["elements"][0]
            assert send_btn["action_id"] == "send_gift_leads_dm"
            assert send_btn["value"] == str(prospect_id)


# =============================================================================
# Send DM Reuse Test
# =============================================================================


class TestSendDmReusesPitchedSend:
    """Tests that the DM send flow reuses _send_pitched_message_now."""

    @pytest.mark.asyncio
    @patch("app.routers.slack._send_pitched_message_now")
    @patch("app.routers.slack.async_session_factory")
    async def test_send_dm_calls_send_pitched_message_now(
        self, mock_session_factory, mock_send
    ):
        """send_gift_leads_dm_submit should reuse _send_pitched_message_now."""
        from app.routers.slack import _process_send_gift_leads_dm

        prospect_id = uuid.uuid4()
        message_text = "Here are some leads for you!"

        await _process_send_gift_leads_dm(prospect_id, message_text)

        mock_send.assert_called_once_with(prospect_id, message_text)
