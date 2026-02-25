"""Tests for the streamlined Gift Leads flow.

Tests the 3-click flow: Confirm ICP -> Review Leads -> Send DM.
Also tests Google Sheets integration and auto-trigger for buying signals.
"""

import json
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
    async def test_results_include_send_button_with_json_value(self):
        """Send button value should be JSON with prospect_id and sheet_url."""
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
                sheet_url="https://docs.google.com/spreadsheets/d/abc123",
            )

            # Check the blocks include a send button with JSON value
            call_kwargs = mock_client.chat_postMessage.call_args[1]
            blocks = call_kwargs["blocks"]
            actions_blocks = [b for b in blocks if b.get("type") == "actions"]
            assert len(actions_blocks) == 1
            send_btn = actions_blocks[0]["elements"][0]
            assert send_btn["action_id"] == "send_gift_leads_dm"

            # Button value should be JSON containing prospect_id and sheet_url
            btn_value = json.loads(send_btn["value"])
            assert btn_value["prospect_id"] == str(prospect_id)
            assert btn_value["sheet_url"] == "https://docs.google.com/spreadsheets/d/abc123"

    @pytest.mark.asyncio
    async def test_results_include_sheet_link_in_context(self):
        """When sheet_url is provided, context should include a link to the sheet."""
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

            await bot.send_gift_leads_results_with_send_button(
                prospect_id=prospect_id,
                prospect_name="John Doe",
                leads=leads,
                pool_size=100,
                keywords=["ceo"],
                sheet_url="https://docs.google.com/spreadsheets/d/abc123",
            )

            call_kwargs = mock_client.chat_postMessage.call_args[1]
            blocks = call_kwargs["blocks"]
            context_blocks = [b for b in blocks if b.get("type") == "context"]
            assert len(context_blocks) == 1
            context_text = context_blocks[0]["elements"][0]["text"]
            assert "Google Sheet" in context_text
            assert "abc123" in context_text

    @pytest.mark.asyncio
    async def test_results_without_sheet_url(self):
        """When sheet_url is None, button value should still be JSON (with null sheet_url)."""
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

            await bot.send_gift_leads_results_with_send_button(
                prospect_id=prospect_id,
                prospect_name="John Doe",
                leads=leads,
                pool_size=100,
                keywords=["ceo"],
            )

            call_kwargs = mock_client.chat_postMessage.call_args[1]
            blocks = call_kwargs["blocks"]
            actions_blocks = [b for b in blocks if b.get("type") == "actions"]
            send_btn = actions_blocks[0]["elements"][0]
            btn_value = json.loads(send_btn["value"])
            assert btn_value["prospect_id"] == str(prospect_id)
            assert btn_value["sheet_url"] is None


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


# =============================================================================
# Google Sheets Integration Tests
# =============================================================================


class TestGoogleSheetsService:
    """Tests for the Google Sheets service."""

    def test_get_service_returns_none_when_not_configured(self):
        """Should return None when GOOGLE_SERVICE_ACCOUNT_JSON is empty."""
        try:
            from app.services.google_sheets import GoogleSheetsService, GoogleSheetsError
        except BaseException:
            pytest.skip("gspread/google-auth not importable in this environment")

        with patch("app.services.google_sheets.settings") as mock_settings:
            mock_settings.google_service_account_json = ""
            with pytest.raises(GoogleSheetsError, match="not set"):
                GoogleSheetsService()

    def test_create_sheet_returns_url(self):
        """Should create a sheet and return its URL."""
        try:
            from app.services.google_sheets import GoogleSheetsService
        except BaseException:
            pytest.skip("gspread/google-auth not importable in this environment")

        with patch("app.services.google_sheets.gspread") as mock_gspread, \
             patch("app.services.google_sheets.Credentials") as mock_creds_cls:

            # Mock credentials
            mock_creds = MagicMock()
            mock_creds_cls.from_service_account_info.return_value = mock_creds

            # Mock gspread
            mock_gc = MagicMock()
            mock_gspread.authorize.return_value = mock_gc

            mock_spreadsheet = MagicMock()
            mock_spreadsheet.url = "https://docs.google.com/spreadsheets/d/abc123"
            mock_gc.create.return_value = mock_spreadsheet

            mock_worksheet = MagicMock()
            mock_spreadsheet.sheet1 = mock_worksheet

            with patch("app.services.google_sheets.settings") as mock_settings:
                mock_settings.google_service_account_json = '{"type": "service_account"}'
                svc = GoogleSheetsService()

            leads = [
                {
                    "full_name": "Alice Smith",
                    "job_title": "CEO",
                    "company_name": "TechCorp",
                    "activity_score": 85.0,
                    "linkedin_url": "https://linkedin.com/in/alice",
                }
            ]
            url = svc.create_gift_leads_sheet("John Doe", leads)

            assert url == "https://docs.google.com/spreadsheets/d/abc123"
            mock_gc.create.assert_called_once()
            mock_spreadsheet.share.assert_called_once_with(None, perm_type="anyone", role="reader")
            mock_worksheet.update.assert_called_once()


# =============================================================================
# Auto-Send DM Tests
# =============================================================================


class TestAutoSendGiftLeads:
    """Tests for the auto-send gift leads flow."""

    @pytest.mark.asyncio
    @patch("app.routers.slack._send_pitched_message_now")
    async def test_auto_send_with_sheet_url(self, mock_send):
        """Auto-send should compose DM with Google Sheet link."""
        from app.routers.slack import _auto_send_gift_leads

        prospect_id = uuid.uuid4()
        sheet_url = "https://docs.google.com/spreadsheets/d/abc123"

        await _auto_send_gift_leads(
            prospect_id=prospect_id,
            prospect_name="John Doe",
            leads=[{"full_name": "Lead One", "job_title": "CEO"}],
            sheet_url=sheet_url,
        )

        mock_send.assert_called_once()
        message = mock_send.call_args[0][1]
        assert "Hey John" in message
        assert sheet_url in message

    @pytest.mark.asyncio
    @patch("app.routers.slack._send_pitched_message_now")
    async def test_auto_send_without_sheet_url_falls_back_to_text(self, mock_send):
        """Auto-send without sheet_url should fall back to text list."""
        from app.routers.slack import _auto_send_gift_leads

        prospect_id = uuid.uuid4()

        await _auto_send_gift_leads(
            prospect_id=prospect_id,
            prospect_name="Jane Smith",
            leads=[
                {"full_name": "Lead One", "job_title": "CEO", "company_name": "Acme"},
                {"full_name": "Lead Two", "job_title": "CTO", "company_name": "Beta"},
            ],
            sheet_url=None,
        )

        mock_send.assert_called_once()
        message = mock_send.call_args[0][1]
        assert "Hey Jane" in message
        assert "Lead One" in message
        assert "Lead Two" in message


# =============================================================================
# DM Modal with Sheet URL Test
# =============================================================================


class TestDmModalWithSheetUrl:
    """Tests that the DM modal uses the Google Sheet URL when available."""

    @pytest.mark.asyncio
    @patch("app.routers.slack.async_session_factory")
    @patch("app.routers.slack.get_slack_bot")
    async def test_dm_modal_includes_sheet_url(self, mock_get_bot, mock_session_factory):
        """When sheet_url is provided, DM should contain the sheet link."""
        from app.routers.slack import handle_send_gift_leads_dm

        prospect_id = uuid.uuid4()
        sheet_url = "https://docs.google.com/spreadsheets/d/abc123"

        mock_prospect = MagicMock()
        mock_prospect.id = prospect_id
        mock_prospect.full_name = "John Doe"
        mock_prospect.first_name = "John"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_prospect
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory.return_value = mock_ctx

        mock_bot = AsyncMock()
        mock_get_bot.return_value = mock_bot

        await handle_send_gift_leads_dm(prospect_id, "trigger_123", sheet_url)

        mock_bot.open_send_gift_leads_dm_modal.assert_called_once()
        call_kwargs = mock_bot.open_send_gift_leads_dm_modal.call_args[1]
        assert sheet_url in call_kwargs["draft_dm"]
        assert "Hey John" in call_kwargs["draft_dm"]


# =============================================================================
# LLM-based Search Phrase Derivation Tests
# =============================================================================


class TestDeriveSearchPhrases:
    """Tests for LLM-based multi-word phrase derivation from ICP text."""

    @pytest.mark.asyncio
    @patch("app.services.gift_pipeline.deepseek_calls._get_client")
    async def test_returns_multi_word_phrases(self, mock_get_client):
        """LLM should return multi-word phrases, not single words."""
        from app.services.gift_pipeline.deepseek_calls import derive_search_phrases
        from app.services.gift_pipeline.cost_tracker import CostTracker

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = json.dumps({
            "phrases": ["video platform", "audio platform", "sales enablement", "call center", "voice agent"]
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
        mock_get_client.return_value = mock_client

        ct = CostTracker()
        result = await derive_search_phrases(
            "video & audio platforms for sincerity insights, Role Play / Sales Enablement, Call Centers / Voice Agents",
            ct,
        )

        assert len(result) >= 3
        # Should be multi-word phrases, not single words
        assert any(" " in phrase for phrase in result)
        assert "video platform" in result

    @pytest.mark.asyncio
    @patch("app.services.gift_pipeline.deepseek_calls._get_client")
    async def test_fallback_on_error(self, mock_get_client):
        """Should fall back to delimiter-based splitting on LLM error."""
        from app.services.gift_pipeline.deepseek_calls import derive_search_phrases
        from app.services.gift_pipeline.cost_tracker import CostTracker

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
        mock_get_client.return_value = mock_client

        ct = CostTracker()
        result = await derive_search_phrases(
            "video & audio platforms, Sales Enablement, Call Centers",
            ct,
        )

        # Should still return phrases from fallback
        assert len(result) >= 1
        # Fallback splits on commas/slashes, so should preserve multi-word groups
        assert any("sales enablement" in p.lower() for p in result)

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self):
        """Empty input should return empty list without calling LLM."""
        from app.services.gift_pipeline.deepseek_calls import derive_search_phrases
        from app.services.gift_pipeline.cost_tracker import CostTracker

        ct = CostTracker()
        result = await derive_search_phrases("", ct)
        assert result == []

    def test_fallback_derive_phrases(self):
        """Fallback should split on commas/slashes and preserve multi-word groups."""
        from app.services.gift_pipeline.deepseek_calls import _fallback_derive_phrases

        result = _fallback_derive_phrases(
            "video & audio platforms, Sales Enablement, Call Centers / Voice Agents"
        )
        assert len(result) >= 2
        # Should preserve multi-word groups
        assert any("sales enablement" in p for p in result)


# =============================================================================
# ICP Re-qualification Tests
# =============================================================================


class TestIcpRequalification:
    """Tests that post-search ICP re-qualification filters non-matches."""

    @pytest.mark.asyncio
    @patch("app.routers.slack.get_slack_bot")
    @patch("app.routers.slack.async_session_factory")
    async def test_filters_non_matching_leads(
        self, mock_session_factory, mock_get_bot
    ):
        """Leads that don't match ICP should be filtered out."""
        from app.routers.slack import _process_gift_leads_with_send

        prospect_id = uuid.uuid4()

        # Create mock prospects in DB
        mock_prospects = []
        for i in range(5):
            p = MagicMock()
            p.full_name = f"Lead {i}"
            p.job_title = f"Title {i}"
            p.company_name = f"Company {i}"
            p.location = "USA"
            p.headline = f"Headline {i}"
            p.activity_score = 80 - i
            p.icp_reason = ""
            p.linkedin_url = f"https://linkedin.com/in/lead{i}"
            mock_prospects.append(p)

        mock_session = AsyncMock()
        # First call: pool count, Second call: prospect query
        mock_pool_result = MagicMock()
        mock_pool_result.scalar.return_value = 100
        mock_query_result = MagicMock()
        mock_query_result.scalars.return_value.all.return_value = mock_prospects
        mock_session.execute = AsyncMock(side_effect=[mock_pool_result, mock_query_result])

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_factory.return_value = mock_ctx

        mock_bot = AsyncMock()
        mock_get_bot.return_value = mock_bot

        # Mock check_icp_match: only Lead 0 and Lead 2 match
        async def mock_icp_check(lead, cost_tracker, icp_criteria=None):
            name = lead.get("full_name", "")
            if name in ("Lead 0", "Lead 2"):
                return {"match": True, "confidence": "high", "reason": "Matches ICP"}
            return {"match": False, "confidence": "high", "reason": "Does not match"}

        with patch("app.services.gift_pipeline.deepseek_calls.check_icp_match", side_effect=mock_icp_check), \
             patch("app.services.google_sheets.get_google_sheets_service", return_value=None):
            await _process_gift_leads_with_send(
                prospect_id=prospect_id,
                keywords=["title"],
                prospect_name="Test Prospect",
                icp_description="video platform leaders",
            )

        # Should have posted results with only the 2 matching leads
        mock_bot.send_gift_leads_ready.assert_called_once()
        call_kwargs = mock_bot.send_gift_leads_ready.call_args[1]
        # lead_count should reflect filtered results
        assert call_kwargs["lead_count"] == 2


# =============================================================================
# Modal Submit Extracts ICP Description Tests
# =============================================================================


class TestModalSubmitExtractsIcp:
    """Tests that confirm_icp_gift_leads_submit extracts ICP description."""

    def test_icp_text_extracted_from_modal_values(self):
        """Verify the modal values structure we expect for icp_input block."""
        # Simulate the Slack modal values structure
        values = {
            "icp_input": {
                "icp_text": {
                    "type": "plain_text_input",
                    "value": "video & audio platforms for sincerity insights",
                }
            },
            "keywords_input": {
                "keywords_text": {
                    "type": "plain_text_input",
                    "value": "video platform, audio platform",
                }
            },
        }

        # Extract the same way the handler does
        icp_text = (
            values.get("icp_input", {})
            .get("icp_text", {})
            .get("value", "")
        )
        keywords_text = (
            values.get("keywords_input", {})
            .get("keywords_text", {})
            .get("value", "")
        )

        assert icp_text == "video & audio platforms for sincerity insights"
        assert keywords_text == "video platform, audio platform"

    def test_icp_text_empty_when_not_provided(self):
        """ICP text should be empty string when block is missing."""
        values = {
            "keywords_input": {
                "keywords_text": {
                    "type": "plain_text_input",
                    "value": "video platform",
                }
            },
        }

        icp_text = (
            values.get("icp_input", {})
            .get("icp_text", {})
            .get("value", "")
        )

        assert icp_text == ""
