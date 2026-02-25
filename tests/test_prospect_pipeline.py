"""Tests for competitor post pipeline (prospect_pipeline.py)."""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.prospect_pipeline import (
    build_google_search_query,
    extract_reaction_count,
    filter_posts_by_reactions,
    is_likely_english,
    prefilter_engagers_by_headline,
    aggregate_and_deduplicate_urls,
    filter_already_processed,
    normalize_supreme_coder_profile,
    filter_by_location,
    is_profile_complete,
    filter_complete_profiles,
    qualify_leads_with_deepseek,
    create_prospect_records,
    _normalize_url,
    _estimate_costs,
    run_competitor_post_pipeline,
)


# ---------------------------------------------------------------------------
# Step 1: Google search query
# ---------------------------------------------------------------------------

class TestBuildGoogleSearchQuery:

    def test_basic_query(self):
        q = build_google_search_query("ceos", days_back=7)
        assert 'site:linkedin.com/posts "ceos" after:' in q

    def test_date_format(self):
        q = build_google_search_query("founders", days_back=14)
        # Should contain a valid date
        assert "after:20" in q


# ---------------------------------------------------------------------------
# Step 2: Reaction filtering
# ---------------------------------------------------------------------------

class TestExtractReactionCount:

    def test_basic(self):
        assert extract_reaction_count("150+ reactions") == 150

    def test_with_commas(self):
        assert extract_reaction_count("1,234+ reactions") == 1234

    def test_without_plus(self):
        assert extract_reaction_count("50 reactions") == 50

    def test_none_input(self):
        assert extract_reaction_count(None) == 0

    def test_empty_string(self):
        assert extract_reaction_count("") == 0

    def test_no_match(self):
        assert extract_reaction_count("some random text") == 0


class TestFilterPostsByReactions:

    def test_filters_below_threshold(self):
        posts = [
            {"followersAmount": "150+ reactions", "url": "https://linkedin.com/posts/abc"},
            {"followersAmount": "20+ reactions", "url": "https://linkedin.com/posts/def"},
        ]
        result = filter_posts_by_reactions(posts, min_reactions=50)
        assert len(result) == 1

    def test_keeps_above_threshold(self):
        posts = [
            {"followersAmount": "200+ reactions", "url": "https://linkedin.com/posts/abc"},
        ]
        result = filter_posts_by_reactions(posts, min_reactions=50)
        assert len(result) == 1

    def test_includes_no_data_linkedin_posts(self):
        posts = [
            {"url": "https://linkedin.com/posts/abc"},  # no reaction data
        ]
        result = filter_posts_by_reactions(posts, min_reactions=50)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Step 4: Headline pre-filter
# ---------------------------------------------------------------------------

class TestIsLikelyEnglish:

    def test_english_passes(self):
        is_eng, _ = is_likely_english("CEO at Tech Company")
        assert is_eng is True

    def test_cjk_rejects(self):
        is_eng, reason = is_likely_english("\u4e2d\u6587\u516c\u53f8\u7ecf\u7406")
        assert is_eng is False

    def test_cyrillic_rejects(self):
        is_eng, reason = is_likely_english("\u0414\u0438\u0440\u0435\u043a\u0442\u043e\u0440 \u043a\u043e\u043c\u043f\u0430\u043d\u0438\u0438")
        assert is_eng is False

    def test_arabic_rejects(self):
        is_eng, reason = is_likely_english("\u0645\u062f\u064a\u0631 \u0634\u0631\u0643\u0629 \u0643\u0628\u064a\u0631\u0629")
        assert is_eng is False

    def test_non_english_indicator(self):
        is_eng, reason = is_likely_english("Directeur Marketing")
        assert is_eng is False
        assert "directeur" in reason

    def test_short_text_passes(self):
        is_eng, _ = is_likely_english("Hi")
        assert is_eng is True


class TestPrefilterEngagersByHeadline:

    def test_rejects_students(self):
        engagers = [
            {"reactor": {"headline": "Student at MIT", "name": "John"}},
            {"reactor": {"headline": "CEO at Acme", "name": "Jane"}},
        ]
        filtered, kept, rejected, non_eng = prefilter_engagers_by_headline(engagers)
        assert kept == 1
        assert rejected == 1
        assert filtered[0]["reactor"]["name"] == "Jane"

    def test_keeps_no_headline(self):
        engagers = [
            {"reactor": {"headline": "", "name": "Unknown"}},
        ]
        filtered, kept, _, _ = prefilter_engagers_by_headline(engagers)
        assert kept == 1

    def test_rejects_non_english(self):
        engagers = [
            {"reactor": {"headline": "Directeur at Company", "name": "Pierre"}},
        ]
        filtered, _, _, non_eng = prefilter_engagers_by_headline(engagers)
        assert non_eng == 1
        assert len(filtered) == 0


# ---------------------------------------------------------------------------
# Step 5: Dedup URLs
# ---------------------------------------------------------------------------

class TestAggregateAndDeduplicateUrls:

    def test_deduplicates(self):
        engagers = [
            {"reactor": {"profile_url": "https://linkedin.com/in/alice"}},
            {"reactor": {"profile_url": "https://linkedin.com/in/alice"}},
            {"reactor": {"profile_url": "https://linkedin.com/in/bob"}},
        ]
        urls = aggregate_and_deduplicate_urls(engagers)
        assert len(urls) == 2

    def test_preserves_order(self):
        engagers = [
            {"reactor": {"profile_url": "https://linkedin.com/in/bob"}},
            {"reactor": {"profile_url": "https://linkedin.com/in/alice"}},
        ]
        urls = aggregate_and_deduplicate_urls(engagers)
        assert urls[0] == "https://linkedin.com/in/bob"


# ---------------------------------------------------------------------------
# Step 6: DB dedup
# ---------------------------------------------------------------------------

class TestFilterAlreadyProcessed:

    @pytest.mark.asyncio
    async def test_filters_existing_urls(self):
        """Existing prospects should be filtered out."""
        mock_session = AsyncMock()
        # Simulate one URL already in DB
        mock_result = MagicMock()
        mock_result.all.return_value = [("https://linkedin.com/in/existing",)]
        mock_session.execute.return_value = mock_result

        urls = [
            "https://linkedin.com/in/existing",
            "https://linkedin.com/in/new",
        ]
        new_urls = await filter_already_processed(urls, mock_session)
        assert len(new_urls) == 1
        assert "new" in new_urls[0]

    @pytest.mark.asyncio
    async def test_empty_input(self):
        mock_session = AsyncMock()
        result = await filter_already_processed([], mock_session)
        assert result == []


# ---------------------------------------------------------------------------
# Step 7: Profile normalization
# ---------------------------------------------------------------------------

class TestNormalizeSupremeCoderProfile:

    def test_basic_normalization(self):
        raw = {
            "inputUrl": "https://linkedin.com/in/john",
            "firstName": "John",
            "lastName": "Doe",
            "headline": "CEO at Acme",
            "summary": "Experienced leader",
            "jobTitle": "CEO",
            "companyName": "Acme Corp",
            "geoCountryName": "United States",
            "geoLocationName": "San Francisco, California, United States",
            "connectionsCount": 500,
            "followerCount": 1000,
            "positions": [],
        }
        profile = normalize_supreme_coder_profile(raw)
        assert profile["fullName"] == "John Doe"
        assert profile["jobTitle"] == "CEO"
        assert profile["companyName"] == "Acme Corp"
        assert profile["addressCountryOnly"] == "United States"
        assert profile["addressWithoutCountry"] == "San Francisco, California"

    def test_positions_fallback(self):
        raw = {
            "inputUrl": "https://linkedin.com/in/jane",
            "firstName": "Jane",
            "lastName": "Smith",
            "headline": "Founder",
            "summary": "",
            "positions": [
                {"title": "Founder", "company": {"name": "StartupX"}},
            ],
        }
        profile = normalize_supreme_coder_profile(raw)
        assert profile["jobTitle"] == "Founder"
        assert profile["companyName"] == "StartupX"

    def test_grouped_positions(self):
        raw = {
            "inputUrl": "https://linkedin.com/in/test",
            "firstName": "Test",
            "lastName": "User",
            "positions": [
                {
                    "company": {"name": "BigCo"},
                    "positions": [
                        {"title": "VP Sales"},
                        {"title": "Director Sales"},
                    ],
                },
            ],
        }
        profile = normalize_supreme_coder_profile(raw)
        assert profile["jobTitle"] == "VP Sales"
        assert profile["companyName"] == "BigCo"
        assert profile["experiencesCount"] == 2


# ---------------------------------------------------------------------------
# Step 8: Location filter
# ---------------------------------------------------------------------------

class TestFilterByLocation:

    def test_keeps_us_canada(self):
        profiles = [
            {"fullName": "Alice", "addressCountryOnly": "United States"},
            {"fullName": "Bob", "addressCountryOnly": "Canada"},
            {"fullName": "Carlos", "addressCountryOnly": "Brazil"},
        ]
        result = filter_by_location(profiles, ["United States", "Canada"])
        assert len(result) == 2

    def test_case_insensitive(self):
        profiles = [{"fullName": "Test", "addressCountryOnly": "united states"}]
        result = filter_by_location(profiles, ["United States"])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Step 9: Completeness filter
# ---------------------------------------------------------------------------

class TestIsProfileComplete:

    def test_complete_with_job_info(self):
        lead = {"jobTitle": "CEO", "companyName": "Acme"}
        assert is_profile_complete(lead) is True

    def test_complete_with_headline_and_experience(self):
        lead = {"headline": "CEO at Acme", "experiences": [{"title": "CEO"}]}
        assert is_profile_complete(lead) is True

    def test_incomplete_no_data(self):
        lead = {"headline": "", "jobTitle": "", "companyName": ""}
        assert is_profile_complete(lead) is False

    def test_incomplete_placeholder_headline(self):
        lead = {"headline": "--", "jobTitle": "", "companyName": ""}
        assert is_profile_complete(lead) is False


class TestFilterCompleteProfiles:

    def test_filters_incomplete(self):
        leads = [
            {"fullName": "Complete", "jobTitle": "CEO", "companyName": "Acme"},
            {"fullName": "Incomplete", "headline": "", "jobTitle": "", "companyName": ""},
        ]
        result = filter_complete_profiles(leads)
        assert len(result) == 1
        assert result[0]["fullName"] == "Complete"


# ---------------------------------------------------------------------------
# Step 10: ICP qualification (mocked)
# ---------------------------------------------------------------------------

class TestQualifyLeadsWithDeepseek:

    @pytest.mark.asyncio
    async def test_qualifies_matching_leads(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"match": true, "confidence": "high", "reason": "CEO qualifies"}'}}]
        }

        leads = [{"fullName": "Alice", "jobTitle": "CEO", "companyName": "Acme"}]

        with patch("app.services.prospect_pipeline.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.post.return_value = mock_response
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client

            result = await qualify_leads_with_deepseek(leads)

        assert len(result) == 1
        assert result[0]["icp_match"] is True

    @pytest.mark.asyncio
    async def test_rejects_non_matching_leads(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"match": false, "confidence": "high", "reason": "Student rejected"}'}}]
        }

        leads = [{"fullName": "Bob", "jobTitle": "Student", "companyName": "MIT"}]

        with patch("app.services.prospect_pipeline.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.post.return_value = mock_response
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client

            result = await qualify_leads_with_deepseek(leads)

        assert len(result) == 0


# ---------------------------------------------------------------------------
# DB: Create prospect records
# ---------------------------------------------------------------------------

class TestCreateProspectRecords:

    @pytest.mark.asyncio
    async def test_creates_records(self):
        mock_session = AsyncMock()
        # No existing records
        mock_result = MagicMock()
        mock_result.scalar.return_value = None
        mock_session.execute.return_value = mock_result

        leads = [
            {
                "linkedinUrl": "https://linkedin.com/in/alice",
                "firstName": "Alice",
                "lastName": "Smith",
                "jobTitle": "CEO",
                "companyName": "Acme",
            },
        ]

        from app.models import ProspectSource
        count = await create_prospect_records(
            leads, ProspectSource.COMPETITOR_POST, "ceos", 480247, mock_session
        )
        assert count == 1
        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_existing(self):
        mock_session = AsyncMock()
        # Already exists
        mock_result = MagicMock()
        mock_result.scalar.return_value = uuid4()
        mock_session.execute.return_value = mock_result

        leads = [{"linkedinUrl": "https://linkedin.com/in/existing"}]

        from app.models import ProspectSource
        count = await create_prospect_records(
            leads, ProspectSource.COMPETITOR_POST, "ceos", 480247, mock_session
        )
        assert count == 0


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

class TestEstimateCosts:

    def test_basic_costs(self):
        counts = {
            "google_results": 10,
            "posts_scraped": 5,
            "profiles_scraped": 20,
            "icp_checks": 15,
            "personalizations": 10,
        }
        costs = _estimate_costs(counts)
        assert costs["cost_total"] > Decimal("0")
        assert costs["cost_apify_google"] == Decimal(str(round(10 * 0.004, 4)))


# ---------------------------------------------------------------------------
# Integration: Pipeline orchestrator
# ---------------------------------------------------------------------------

class TestRunCompetitorPostPipeline:

    @pytest.mark.asyncio
    async def test_happy_path(self):
        """Full pipeline with all steps mocked returns a completed summary."""
        mock_session = AsyncMock()
        mock_pipeline_run = MagicMock()
        mock_pipeline_run.id = uuid4()
        mock_pipeline_run.started_at = datetime.now()

        with patch("app.database.async_session_factory") as mock_sf, \
             patch("app.services.prospect_pipeline.search_google_linkedin_posts") as mock_search, \
             patch("app.services.prospect_pipeline.scrape_post_engagers") as mock_engagers, \
             patch("app.services.prospect_pipeline.scrape_linkedin_profiles") as mock_profiles, \
             patch("app.services.prospect_pipeline.qualify_leads_with_deepseek") as mock_icp, \
             patch("app.services.prospect_pipeline.generate_personalization_deepseek") as mock_pers, \
             patch("app.services.prospect_pipeline.validate_and_fix_batch") as mock_validate, \
             patch("app.services.prospect_pipeline.upload_to_heyreach") as mock_upload, \
             patch("app.services.prospect_pipeline.create_prospect_records") as mock_create, \
             patch("app.services.prospect_pipeline._send_pipeline_summary") as mock_slack:

            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            # Make PipelineRun creation work
            mock_session.add.side_effect = lambda obj: setattr(obj, 'id', mock_pipeline_run.id) or setattr(obj, 'started_at', datetime.now(timezone.utc))

            mock_search.return_value = [
                {"url": "https://linkedin.com/posts/abc", "followersAmount": "200+ reactions"},
            ]
            mock_engagers.return_value = [
                {"reactor": {"profile_url": "https://linkedin.com/in/alice", "headline": "CEO at Acme"}},
            ]
            # DB dedup - no existing
            mock_result = MagicMock()
            mock_result.all.return_value = []
            mock_session.execute.return_value = mock_result

            mock_profiles.return_value = [
                {
                    "linkedinUrl": "https://linkedin.com/in/alice",
                    "firstName": "Alice",
                    "lastName": "Smith",
                    "fullName": "Alice Smith",
                    "headline": "CEO at Acme",
                    "jobTitle": "CEO",
                    "companyName": "Acme",
                    "addressCountryOnly": "United States",
                    "addressWithCountry": "San Francisco, CA, US",
                    "about": "Tech leader",
                    "experiences": [{"title": "CEO"}],
                    "experiencesCount": 1,
                },
            ]
            mock_icp.return_value = [
                {
                    "linkedinUrl": "https://linkedin.com/in/alice",
                    "firstName": "Alice",
                    "lastName": "Smith",
                    "fullName": "Alice Smith",
                    "headline": "CEO at Acme",
                    "jobTitle": "CEO",
                    "companyName": "Acme",
                    "addressWithCountry": "San Francisco, CA, US",
                    "about": "Tech leader",
                    "icp_match": True,
                },
            ]
            mock_pers.return_value = "Hey Alice\n\nAcme looks interesting\n..."
            mock_validate.return_value = [
                {
                    "linkedinUrl": "https://linkedin.com/in/alice",
                    "firstName": "Alice",
                    "fullName": "Alice Smith",
                    "personalized_message": "Hey Alice\n\nAcme looks interesting\n...",
                },
            ]
            mock_upload.return_value = 1
            mock_create.return_value = 1

            result = await run_competitor_post_pipeline(dry_run=False)

        assert result["status"] == "completed"
        assert result["uploaded"] == 1
        mock_upload.assert_awaited_once()
        mock_slack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_posts_found(self):
        """Pipeline exits early when no posts are found."""
        mock_session = AsyncMock()

        with patch("app.database.async_session_factory") as mock_sf, \
             patch("app.services.prospect_pipeline.search_google_linkedin_posts") as mock_search:

            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session.add.side_effect = lambda obj: setattr(obj, 'id', uuid4()) or setattr(obj, 'started_at', datetime.now(timezone.utc))

            mock_search.return_value = []

            result = await run_competitor_post_pipeline()

        assert result["status"] == "completed"
        assert result["posts_found"] == 0

    @pytest.mark.asyncio
    async def test_dry_run_skips_upload(self):
        """Dry run mode skips HeyReach upload."""
        mock_session = AsyncMock()

        with patch("app.database.async_session_factory") as mock_sf, \
             patch("app.services.prospect_pipeline.search_google_linkedin_posts") as mock_search, \
             patch("app.services.prospect_pipeline.scrape_post_engagers") as mock_engagers, \
             patch("app.services.prospect_pipeline.scrape_linkedin_profiles") as mock_profiles, \
             patch("app.services.prospect_pipeline.qualify_leads_with_deepseek") as mock_icp, \
             patch("app.services.prospect_pipeline.generate_personalization_deepseek") as mock_pers, \
             patch("app.services.prospect_pipeline.validate_and_fix_batch") as mock_validate, \
             patch("app.services.prospect_pipeline.upload_to_heyreach") as mock_upload, \
             patch("app.services.prospect_pipeline.create_prospect_records") as mock_create, \
             patch("app.services.prospect_pipeline._send_pipeline_summary") as mock_slack:

            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()
            mock_session.add.side_effect = lambda obj: setattr(obj, 'id', uuid4()) or setattr(obj, 'started_at', datetime.now(timezone.utc))

            mock_search.return_value = [{"url": "https://linkedin.com/posts/x", "followersAmount": "100+ reactions"}]
            mock_engagers.return_value = [{"reactor": {"profile_url": "https://linkedin.com/in/a", "headline": "CEO"}}]
            mock_result = MagicMock()
            mock_result.all.return_value = []
            mock_session.execute.return_value = mock_result
            mock_profiles.return_value = [{
                "linkedinUrl": "https://linkedin.com/in/a", "firstName": "A", "fullName": "A B",
                "headline": "CEO", "jobTitle": "CEO", "companyName": "X",
                "addressCountryOnly": "United States", "addressWithCountry": "NY, US",
                "about": "", "experiences": [{"title": "CEO"}], "experiencesCount": 1,
            }]
            mock_icp.return_value = [mock_profiles.return_value[0] | {"icp_match": True}]
            mock_pers.return_value = "Hey A..."
            mock_validate.return_value = [{"personalized_message": "Hey A...", "linkedinUrl": "https://linkedin.com/in/a", "firstName": "A", "fullName": "A B"}]
            mock_create.return_value = 1

            result = await run_competitor_post_pipeline(dry_run=True)

        assert result["status"] == "completed"
        assert result["uploaded"] == 0
        mock_upload.assert_not_awaited()
