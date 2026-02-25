"""Tests for lead finder pipeline (lead_finder_pipeline.py)."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.lead_finder_pipeline import (
    find_leads_apify,
    run_lead_finder_pipeline,
    _process_surplus_leads,
    DEFAULT_JOB_TITLES,
    DEFAULT_COMPANY_KEYWORDS,
    LEADS_FINDER_ACTOR,
)


# ---------------------------------------------------------------------------
# Step 1: Find leads via Apify
# ---------------------------------------------------------------------------

class TestFindLeadsApify:

    @pytest.mark.asyncio
    async def test_normalizes_field_names(self):
        """Apify results should be normalized to camelCase conventions."""
        mock_items = [
            {
                "first_name": "Alice",
                "last_name": "Smith",
                "email": "alice@acme.com",
                "job_title": "CEO",
                "company_name": "Acme Corp",
                "linkedin_url": "https://linkedin.com/in/alice",
                "location": "New York, US",
                "company_domain": "acme.com",
            },
        ]

        with patch("app.services.lead_finder_pipeline.run_apify_actor") as mock_actor:
            mock_actor.return_value = mock_items

            result = await find_leads_apify(
                ["CEO"], ["agency"], "united states", 25
            )

        assert len(result) == 1
        lead = result[0]
        assert lead["firstName"] == "Alice"
        assert lead["lastName"] == "Smith"
        assert lead["jobTitle"] == "CEO"
        assert lead["companyName"] == "Acme Corp"
        assert lead["email"] == "alice@acme.com"
        assert "linkedin.com/in/alice" in lead["linkedinUrl"]

    @pytest.mark.asyncio
    async def test_correct_actor_input(self):
        """Verify the exact Apify actor input matches scrape_apify.py format."""
        with patch("app.services.lead_finder_pipeline.run_apify_actor") as mock_actor:
            mock_actor.return_value = []

            await find_leads_apify(
                ["CEO", "Founder"],
                ["SaaS", "agency"],
                "united states",
                100,
                require_email=True,
            )

        mock_actor.assert_awaited_once()
        call_args = mock_actor.call_args
        actor_id = call_args[0][0]
        input_payload = call_args[0][1]

        assert actor_id == LEADS_FINDER_ACTOR
        assert input_payload["contact_job_title"] == ["CEO", "Founder"]
        assert input_payload["company_keywords"] == ["SaaS", "agency"]
        assert input_payload["contact_location"] == ["united states"]
        assert input_payload["language"] == "en"
        assert input_payload["fetch_count"] == 100
        assert input_payload["email_status"] == ["validated"]

    @pytest.mark.asyncio
    async def test_no_email_filter(self):
        """When require_email=False, email_status should not be in input."""
        with patch("app.services.lead_finder_pipeline.run_apify_actor") as mock_actor:
            mock_actor.return_value = []

            await find_leads_apify(
                ["CEO"], ["agency"], "united states", 50, require_email=False,
            )

        input_payload = mock_actor.call_args[0][1]
        assert "email_status" not in input_payload

    @pytest.mark.asyncio
    async def test_skips_items_without_linkedin_url(self):
        """Items missing linkedin_url should be dropped."""
        mock_items = [
            {"first_name": "NoUrl", "last_name": "Person"},
            {"first_name": "HasUrl", "last_name": "Person", "linkedin_url": "https://linkedin.com/in/hasurl"},
        ]

        with patch("app.services.lead_finder_pipeline.run_apify_actor") as mock_actor:
            mock_actor.return_value = mock_items

            result = await find_leads_apify(["CEO"], ["agency"], "us", 50)

        assert len(result) == 1
        assert result[0]["firstName"] == "HasUrl"


# ---------------------------------------------------------------------------
# Surplus processing
# ---------------------------------------------------------------------------

class TestProcessSurplusLeads:

    @pytest.mark.asyncio
    async def test_no_surplus(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        personalized, uploaded = await _process_surplus_leads(10, 480247, mock_session)
        assert personalized == 0
        assert uploaded == 0


# ---------------------------------------------------------------------------
# Integration: Pipeline orchestrator
# ---------------------------------------------------------------------------

class TestRunLeadFinderPipeline:

    @pytest.mark.asyncio
    async def test_happy_path(self):
        """Full pipeline with all steps mocked returns completed summary."""
        mock_session = AsyncMock()

        with patch("app.database.async_session_factory") as mock_sf, \
             patch("app.services.lead_finder_pipeline._process_surplus_leads") as mock_surplus, \
             patch("app.services.lead_finder_pipeline.find_leads_apify") as mock_find, \
             patch("app.services.lead_finder_pipeline.filter_already_processed") as mock_dedup, \
             patch("app.services.lead_finder_pipeline.qualify_leads_with_deepseek") as mock_icp, \
             patch("app.services.lead_finder_pipeline.generate_personalization_deepseek") as mock_pers, \
             patch("app.services.lead_finder_pipeline.upload_to_heyreach") as mock_upload, \
             patch("app.services.lead_finder_pipeline.create_prospect_records") as mock_create, \
             patch("app.services.lead_finder_pipeline._send_pipeline_summary") as mock_slack:

            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session.add.side_effect = lambda obj: setattr(obj, 'id', uuid4()) or setattr(obj, 'started_at', datetime.now(timezone.utc))

            mock_surplus.return_value = (0, 0)

            mock_find.return_value = [
                {
                    "linkedinUrl": "https://linkedin.com/in/alice",
                    "firstName": "Alice",
                    "lastName": "Smith",
                    "fullName": "Alice Smith",
                    "jobTitle": "CEO",
                    "companyName": "Acme",
                    "headline": "CEO at Acme",
                    "addressWithCountry": "New York, US",
                    "email": "alice@acme.com",
                },
            ]
            mock_dedup.return_value = ["https://linkedin.com/in/alice"]
            mock_icp.return_value = [
                {
                    "linkedinUrl": "https://linkedin.com/in/alice",
                    "firstName": "Alice",
                    "fullName": "Alice Smith",
                    "jobTitle": "CEO",
                    "companyName": "Acme",
                    "headline": "CEO at Acme",
                    "addressWithCountry": "New York, US",
                    "icp_match": True,
                },
            ]
            mock_pers.return_value = "Hey Alice\n\nAcme looks interesting..."
            mock_upload.return_value = 1
            mock_create.return_value = 1

            result = await run_lead_finder_pipeline(fetch_count=25)

        assert result["status"] == "completed"
        assert result["uploaded"] == 1
        mock_upload.assert_awaited_once()
        mock_slack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dry_run_skips_upload(self):
        """Dry run skips HeyReach upload and surplus processing."""
        mock_session = AsyncMock()

        with patch("app.database.async_session_factory") as mock_sf, \
             patch("app.services.lead_finder_pipeline.find_leads_apify") as mock_find, \
             patch("app.services.lead_finder_pipeline.filter_already_processed") as mock_dedup, \
             patch("app.services.lead_finder_pipeline.qualify_leads_with_deepseek") as mock_icp, \
             patch("app.services.lead_finder_pipeline.generate_personalization_deepseek") as mock_pers, \
             patch("app.services.lead_finder_pipeline.upload_to_heyreach") as mock_upload, \
             patch("app.services.lead_finder_pipeline.create_prospect_records") as mock_create, \
             patch("app.services.lead_finder_pipeline._send_pipeline_summary") as mock_slack:

            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session.add.side_effect = lambda obj: setattr(obj, 'id', uuid4()) or setattr(obj, 'started_at', datetime.now(timezone.utc))

            mock_find.return_value = [
                {"linkedinUrl": "https://linkedin.com/in/a", "firstName": "A", "fullName": "A B",
                 "jobTitle": "CEO", "companyName": "X", "headline": "CEO", "addressWithCountry": "NY, US"},
            ]
            mock_dedup.return_value = ["https://linkedin.com/in/a"]
            mock_icp.return_value = [
                {"linkedinUrl": "https://linkedin.com/in/a", "firstName": "A", "fullName": "A B",
                 "jobTitle": "CEO", "companyName": "X", "icp_match": True},
            ]
            mock_pers.return_value = "Hey A..."
            mock_create.return_value = 1

            result = await run_lead_finder_pipeline(fetch_count=10, dry_run=True)

        assert result["status"] == "completed"
        assert result["uploaded"] == 0
        mock_upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_surplus_sufficient(self):
        """When surplus covers the needed count, no new scraping happens."""
        mock_session = AsyncMock()

        with patch("app.database.async_session_factory") as mock_sf, \
             patch("app.services.lead_finder_pipeline._process_surplus_leads") as mock_surplus, \
             patch("app.services.lead_finder_pipeline.find_leads_apify") as mock_find, \
             patch("app.services.lead_finder_pipeline._send_pipeline_summary") as mock_slack:

            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session.add.side_effect = lambda obj: setattr(obj, 'id', uuid4()) or setattr(obj, 'started_at', datetime.now(timezone.utc))

            # Surplus covers the need
            mock_surplus.return_value = (30, 30)

            result = await run_lead_finder_pipeline(fetch_count=25)

        assert result["status"] == "completed"
        assert result["source"] == "surplus"
        assert result["surplus_uploaded"] == 30
        mock_find.assert_not_awaited()
