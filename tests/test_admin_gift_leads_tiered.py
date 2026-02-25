"""Tests for tiered gift leads admin endpoint.

Tests the 3-tier approach:
- Tier 1: DB pool + strict ICP re-qualification
- Tier 2: Full gift leads pipeline fallback
- Tier 3: Lead finder / Sales Nav fallback
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Patch targets: lazy imports in admin_trigger_gift_leads resolve from source modules
_SLACK_BOT = "app.services.slack.get_slack_bot"
_SHEETS_SVC = "app.services.google_sheets.get_google_sheets_service"
_SESSION = "app.main.async_session_factory"
_ICP_CHECK = "app.services.gift_pipeline.deepseek_calls.check_icp_match"


def _make_mock_prospect(name, job_title="CEO", headline="Tech CEO", score=80, url=None):
    p = MagicMock()
    p.full_name = name
    p.job_title = job_title
    p.company_name = "TestCorp"
    p.location = "USA"
    p.headline = headline
    p.activity_score = score
    p.linkedin_url = url or f"https://linkedin.com/in/{name.lower().replace(' ', '-')}"
    p.icp_reason = ""
    return p


def _make_mock_conversation(name, linkedin_url=None):
    conv = MagicMock()
    conv.id = uuid.uuid4()
    conv.lead_name = name
    conv.linkedin_profile_url = linkedin_url or f"https://linkedin.com/in/{name.lower().replace(' ', '-')}"
    conv.updated_at = MagicMock()
    return conv


def _setup_session_mock(conv, prospect, leads_list):
    """Build a mock async session returning conv, prospect, leads, pool_size."""
    mock_session = AsyncMock()
    mock_conv_result = MagicMock()
    mock_conv_result.scalar_one_or_none.return_value = conv
    mock_prospect_result = MagicMock()
    mock_prospect_result.scalar_one_or_none.return_value = prospect
    mock_leads_result = MagicMock()
    mock_leads_result.scalars.return_value.all.return_value = leads_list
    mock_pool_result = MagicMock()
    mock_pool_result.scalar.return_value = 500
    mock_session.execute = AsyncMock(
        side_effect=[mock_conv_result, mock_prospect_result, mock_leads_result, mock_pool_result]
    )

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


# =============================================================================
# Tier 1: ICP Re-qualification
# =============================================================================


class TestTier1IcpRequalification:
    """Tier 1: DB pool search + ICP re-qualification via DeepSeek."""

    @pytest.mark.asyncio
    @patch(_SLACK_BOT)
    @patch(_SHEETS_SVC)
    @patch(_ICP_CHECK)
    @patch(_SESSION)
    async def test_icp_requalification_filters_non_matching_leads(
        self, mock_session_factory, mock_icp, mock_sheets, mock_slack
    ):
        """Non-matching leads should be filtered out by check_icp_match."""
        from app.main import admin_trigger_gift_leads

        conv = _make_mock_conversation("Josh Cantrell")
        prospect = MagicMock()
        prospect.id = uuid.uuid4()
        prospect.linkedin_url = conv.linkedin_profile_url

        # 12 good + 3 bad leads
        leads = [_make_mock_prospect(f"Good Lead {i}", "Tech Founder", score=90 - i) for i in range(12)]
        leads += [_make_mock_prospect(f"Bad Lead {i}", "Credit Manager", score=70 - i) for i in range(3)]

        mock_session_factory.return_value = _setup_session_mock(conv, prospect, leads)

        mock_sheets_svc = MagicMock()
        mock_sheets_svc.create_gift_leads_sheet.return_value = "https://sheets.example.com/abc"
        mock_sheets.return_value = mock_sheets_svc

        mock_bot = AsyncMock()
        mock_slack.return_value = mock_bot

        async def icp_check(lead, cost_tracker, icp_criteria=None):
            if "Good Lead" in lead.get("full_name", ""):
                return {"match": True, "confidence": "high", "reason": "Matches"}
            return {"match": False, "confidence": "high", "reason": "Not ICP"}

        mock_icp.side_effect = icp_check

        result = await admin_trigger_gift_leads(
            prospect_name="josh cantrell",
            keywords="founder,CEO,tech",
            background_tasks=MagicMock(),
            icp_label="B2B tech founders",
        )

        assert result["leads_found"] == 12  # Only good leads
        assert result["tier"] == 1
        mock_bot.send_gift_leads_ready.assert_called_once()

    @pytest.mark.asyncio
    @patch(_SLACK_BOT)
    @patch(_SHEETS_SVC)
    @patch(_ICP_CHECK)
    @patch(_SESSION)
    async def test_enough_qualified_leads_skips_fallback(
        self, mock_session_factory, mock_icp, mock_sheets, mock_slack
    ):
        """When >= min_leads pass ICP check, no fallback is triggered."""
        from app.main import admin_trigger_gift_leads

        conv = _make_mock_conversation("Test Prospect")
        prospect = MagicMock()
        prospect.id = uuid.uuid4()
        prospect.linkedin_url = conv.linkedin_profile_url
        leads = [_make_mock_prospect(f"Lead {i}", "Founder") for i in range(15)]

        mock_session_factory.return_value = _setup_session_mock(conv, prospect, leads)

        mock_sheets_svc = MagicMock()
        mock_sheets_svc.create_gift_leads_sheet.return_value = "https://sheets.example.com/abc"
        mock_sheets.return_value = mock_sheets_svc

        mock_bot = AsyncMock()
        mock_slack.return_value = mock_bot

        mock_icp.return_value = {"match": True}

        bg = MagicMock()
        result = await admin_trigger_gift_leads(
            prospect_name="test prospect",
            keywords="founder",
            background_tasks=bg,
            icp_label="B2B founders",
            min_leads=10,
        )

        assert result["leads_found"] == 15
        assert result["tier"] == 1
        bg.add_task.assert_not_called()


# =============================================================================
# Tier 2: Pipeline Fallback
# =============================================================================


class TestTier2PipelineFallback:
    """Tier 2: Falls back to full 12-step pipeline when Tier 1 is thin."""

    @pytest.mark.asyncio
    @patch(_SLACK_BOT)
    @patch(_SHEETS_SVC)
    @patch(_ICP_CHECK)
    @patch(_SESSION)
    async def test_triggers_pipeline_when_too_few_qualified(
        self, mock_session_factory, mock_icp, mock_sheets, mock_slack
    ):
        """When < min_leads pass ICP, pipeline fallback should be triggered."""
        from app.main import admin_trigger_gift_leads

        conv = _make_mock_conversation("Josh Cantrell", "https://linkedin.com/in/joshcantrell")
        prospect = MagicMock()
        prospect.id = uuid.uuid4()
        prospect.linkedin_url = "https://linkedin.com/in/joshcantrell"

        # Only 3 leads
        leads = [_make_mock_prospect(f"Lead {i}", "Founder") for i in range(3)]

        mock_session_factory.return_value = _setup_session_mock(conv, prospect, leads)
        mock_sheets.return_value = None
        mock_bot = AsyncMock()
        mock_slack.return_value = mock_bot
        mock_icp.return_value = {"match": True}

        bg = MagicMock()
        result = await admin_trigger_gift_leads(
            prospect_name="josh cantrell",
            keywords="founder",
            background_tasks=bg,
            icp_label="B2B tech founders",
            min_leads=10,
        )

        assert result["leads_found"] == 3
        assert result["tier"] == 2
        assert "pipeline" in result["message"].lower()
        bg.add_task.assert_called_once()

    @pytest.mark.asyncio
    @patch(_SLACK_BOT)
    @patch(_SHEETS_SVC)
    @patch(_SESSION)
    async def test_zero_db_leads_triggers_pipeline(
        self, mock_session_factory, mock_sheets, mock_slack
    ):
        """When DB returns 0 leads, pipeline fallback should trigger without ICP check."""
        from app.main import admin_trigger_gift_leads

        conv = _make_mock_conversation("Josh Cantrell", "https://linkedin.com/in/joshcantrell")
        prospect = MagicMock()
        prospect.id = uuid.uuid4()
        prospect.linkedin_url = "https://linkedin.com/in/joshcantrell"

        mock_session_factory.return_value = _setup_session_mock(conv, prospect, [])
        mock_sheets.return_value = None
        mock_bot = AsyncMock()
        mock_slack.return_value = mock_bot

        bg = MagicMock()
        result = await admin_trigger_gift_leads(
            prospect_name="josh cantrell",
            keywords="founder",
            background_tasks=bg,
            icp_label="B2B tech founders",
            min_leads=10,
        )

        assert result["leads_found"] == 0
        assert result["tier"] == 2
        bg.add_task.assert_called_once()


# =============================================================================
# DM Template
# =============================================================================


class TestDmTemplate:
    """Tests that the correct DM template is used across both flows."""

    @pytest.mark.asyncio
    @patch(_SLACK_BOT)
    @patch(_SHEETS_SVC)
    @patch(_ICP_CHECK)
    @patch(_SESSION)
    async def test_admin_uses_icp_template(
        self, mock_session_factory, mock_icp, mock_sheets, mock_slack
    ):
        """Admin endpoint should use the ICP 'showing high intent signals' template."""
        from app.main import admin_trigger_gift_leads

        conv = _make_mock_conversation("Josh Cantrell")
        prospect = MagicMock()
        prospect.id = uuid.uuid4()
        prospect.linkedin_url = conv.linkedin_profile_url
        leads = [_make_mock_prospect(f"Lead {i}", "Founder") for i in range(12)]

        mock_session_factory.return_value = _setup_session_mock(conv, prospect, leads)

        mock_sheets_svc = MagicMock()
        mock_sheets_svc.create_gift_leads_sheet.return_value = "https://sheets.example.com/abc"
        mock_sheets.return_value = mock_sheets_svc

        mock_bot = AsyncMock()
        mock_slack.return_value = mock_bot
        mock_icp.return_value = {"match": True}

        await admin_trigger_gift_leads(
            prospect_name="josh cantrell",
            keywords="founder",
            background_tasks=MagicMock(),
            icp_label="B2B tech founders",
        )

        call_kwargs = mock_bot.send_gift_leads_ready.call_args[1]
        draft_dm = call_kwargs["draft_dm"]
        assert "B2B tech founders showing high intent signals" in draft_dm
        assert "Will be valuable for you" in draft_dm

    @pytest.mark.asyncio
    @patch("app.routers.slack.get_slack_bot")
    @patch("app.routers.slack.async_session_factory")
    async def test_slack_flow_uses_icp_template(
        self, mock_session_factory, mock_get_bot
    ):
        """Slack flow should use the ICP template instead of old 'pulled together' text."""
        from app.routers.slack import _process_gift_leads_with_send

        prospect_id = uuid.uuid4()

        mock_prospects = [_make_mock_prospect(f"Lead {i}", "Founder", score=90 - i) for i in range(12)]

        mock_session = AsyncMock()
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

        async def mock_icp_check(lead, cost_tracker, icp_criteria=None):
            return {"match": True}

        with patch(
            "app.services.gift_pipeline.deepseek_calls.check_icp_match",
            side_effect=mock_icp_check,
        ), patch(
            "app.services.google_sheets.get_google_sheets_service",
        ) as mock_sheets:
            mock_sheets_svc = MagicMock()
            mock_sheets_svc.create_gift_leads_sheet.return_value = "https://sheets.example.com/abc"
            mock_sheets.return_value = mock_sheets_svc

            await _process_gift_leads_with_send(
                prospect_id=prospect_id,
                keywords=["founder"],
                prospect_name="Josh Cantrell",
                icp_description="B2B tech founders",
            )

        call_kwargs = mock_bot.send_gift_leads_ready.call_args[1]
        draft_dm = call_kwargs["draft_dm"]
        assert "showing high intent signals" in draft_dm
        assert "Will be valuable for you" in draft_dm
        assert "pulled together" not in draft_dm


# =============================================================================
# min_leads parameter
# =============================================================================


class TestMinLeadsParam:
    """Tests that min_leads controls the fallback threshold."""

    @pytest.mark.asyncio
    @patch(_SLACK_BOT)
    @patch(_SHEETS_SVC)
    @patch(_ICP_CHECK)
    @patch(_SESSION)
    async def test_custom_min_leads_threshold(
        self, mock_session_factory, mock_icp, mock_sheets, mock_slack
    ):
        """Setting min_leads=5 should not trigger fallback with 6 qualified leads."""
        from app.main import admin_trigger_gift_leads

        conv = _make_mock_conversation("Test User")
        prospect = MagicMock()
        prospect.id = uuid.uuid4()
        prospect.linkedin_url = conv.linkedin_profile_url
        leads = [_make_mock_prospect(f"Lead {i}", "Founder") for i in range(6)]

        mock_session_factory.return_value = _setup_session_mock(conv, prospect, leads)

        mock_sheets_svc = MagicMock()
        mock_sheets_svc.create_gift_leads_sheet.return_value = "https://sheets.example.com/abc"
        mock_sheets.return_value = mock_sheets_svc

        mock_bot = AsyncMock()
        mock_slack.return_value = mock_bot
        mock_icp.return_value = {"match": True}

        bg = MagicMock()
        result = await admin_trigger_gift_leads(
            prospect_name="test user",
            keywords="founder",
            background_tasks=bg,
            icp_label="B2B founders",
            min_leads=5,
        )

        assert result["tier"] == 1
        bg.add_task.assert_not_called()


# =============================================================================
# Dedup and normalize helpers
# =============================================================================


class TestHelpers:
    """Tests for _dedup_leads and _normalize_pipeline_leads."""

    def test_dedup_by_linkedin_url(self):
        from app.main import _dedup_leads

        leads = [
            {"full_name": "A", "linkedin_url": "https://linkedin.com/in/alice"},
            {"full_name": "B", "linkedin_url": "https://linkedin.com/in/bob"},
            {"full_name": "A dup", "linkedin_url": "https://linkedin.com/in/alice"},
        ]
        result = _dedup_leads(leads)
        assert len(result) == 2
        assert result[0]["full_name"] == "A"
        assert result[1]["full_name"] == "B"

    def test_dedup_case_insensitive(self):
        from app.main import _dedup_leads

        leads = [
            {"full_name": "A", "linkedin_url": "https://LinkedIn.com/in/Alice"},
            {"full_name": "A2", "linkedin_url": "https://linkedin.com/in/alice"},
        ]
        result = _dedup_leads(leads)
        assert len(result) == 1

    def test_normalize_pipeline_leads(self):
        from app.main import _normalize_pipeline_leads

        pipeline_leads = [
            {
                "fullName": "Alice",
                "jobTitle": "CTO",
                "companyName": "Acme",
                "addressWithCountry": "UK",
                "headline": "Tech Leader",
                "linkedinUrl": "https://linkedin.com/in/alice",
            }
        ]
        result = _normalize_pipeline_leads(pipeline_leads)
        assert result[0]["full_name"] == "Alice"
        assert result[0]["job_title"] == "CTO"
        assert result[0]["linkedin_url"] == "https://linkedin.com/in/alice"
