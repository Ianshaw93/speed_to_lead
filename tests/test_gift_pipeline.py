"""Tests for the gift leads pipeline."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.gift_pipeline.constants import (
    APIFY_COSTS,
    DEFAULT_COUNTRIES,
    HEADLINE_REJECT_KEYWORDS,
)
from app.services.gift_pipeline.cost_tracker import CostTracker
from app.services.gift_pipeline.filters import (
    aggregate_profile_urls,
    build_engagement_context,
    compute_activity_score,
    deduplicate_profile_urls,
    enrich_profiles_with_engagement,
    extract_activity_fields,
    extract_reaction_count,
    filter_by_location,
    filter_complete_profiles,
    filter_posts_by_reactions,
    is_likely_english,
    is_profile_complete,
    normalize_linkedin_url,
    normalize_supreme_coder_profile,
    prefilter_engagers_by_headline,
)
from app.prompts.gift_leads import (
    get_gift_search_query_prompt,
    get_gift_signal_note_prompt,
    get_prospect_research_prompt,
)


# ============================================================================
# Cost Tracker
# ============================================================================

class TestCostTracker:
    def test_initial_state(self):
        ct = CostTracker()
        assert ct.get_total() == 0.0
        assert ct.counts["google_results"] == 0

    def test_add_google_search(self):
        ct = CostTracker()
        ct.add_google_search(10)
        assert ct.counts["google_results"] == 10
        assert ct.costs["apify_google_search"] == pytest.approx(10 * APIFY_COSTS["google_search"])

    def test_add_profile_scrape(self):
        ct = CostTracker()
        ct.add_profile_scrape(100)
        assert ct.counts["profiles_scraped"] == 100
        assert ct.costs["apify_profile_scraper"] == pytest.approx(100 * APIFY_COSTS["profile_scraper"])

    def test_total_accumulates(self):
        ct = CostTracker()
        ct.add_google_search(10)
        ct.add_profile_scrape(50)
        ct.add_post_reactions(5)
        assert ct.get_total() > 0
        assert ct.get_total() == pytest.approx(
            10 * APIFY_COSTS["google_search"]
            + 50 * APIFY_COSTS["profile_scraper"]
            + 5 * APIFY_COSTS["post_reactions"]
        )

    def test_get_summary_contains_total(self):
        ct = CostTracker()
        ct.add_google_search(5)
        summary = ct.get_summary()
        assert "TOTAL" in summary
        assert "5 results" in summary


# ============================================================================
# Filters: URL normalization
# ============================================================================

class TestNormalizeLinkedinUrl:
    def test_strips_query_params(self):
        assert normalize_linkedin_url("https://linkedin.com/in/john?trk=abc") == "https://linkedin.com/in/john"

    def test_strips_trailing_slash(self):
        assert normalize_linkedin_url("https://linkedin.com/in/john/") == "https://linkedin.com/in/john"

    def test_lowercases(self):
        assert normalize_linkedin_url("https://LinkedIn.com/in/John") == "https://linkedin.com/in/john"


# ============================================================================
# Filters: Profile normalization
# ============================================================================

class TestNormalizeSupremeCoderProfile:
    def test_basic_normalization(self):
        raw = {
            "inputUrl": "https://linkedin.com/in/test",
            "firstName": "John",
            "lastName": "Doe",
            "headline": "CEO at Acme",
            "jobTitle": "CEO",
            "companyName": "Acme Inc",
            "geoCountryName": "United States",
            "geoLocationName": "New York, New York, United States",
            "positions": [],
        }
        result = normalize_supreme_coder_profile(raw)
        assert result["fullName"] == "John Doe"
        assert result["headline"] == "CEO at Acme"
        assert result["jobTitle"] == "CEO"
        assert result["addressCountryOnly"] == "United States"

    def test_flattens_nested_positions(self):
        raw = {
            "inputUrl": "",
            "firstName": "Jane",
            "lastName": "Smith",
            "headline": "",
            "positions": [
                {
                    "company": {"name": "Acme"},
                    "positions": [
                        {"title": "CEO", "description": "Leading"},
                        {"title": "CTO", "description": "Tech"},
                    ],
                }
            ],
        }
        result = normalize_supreme_coder_profile(raw)
        assert result["jobTitle"] == "CEO"  # First position
        assert result["companyName"] == "Acme"
        assert len(result["experiences"]) == 2


# ============================================================================
# Filters: Activity scoring
# ============================================================================

class TestActivityScoring:
    def test_zero_profile(self):
        assert compute_activity_score({}) == 0.0

    def test_max_connections(self):
        score = compute_activity_score({"connectionsCount": 500})
        assert score == 30.0

    def test_engagement_adds_20(self):
        score = compute_activity_score({"engagement_type": "LIKE"})
        assert score == 20.0

    def test_combined_scoring(self):
        profile = {
            "connectionsCount": 250,  # 15 points
            "followersCount": 500,    # 15 points
            "isCreator": True,        # 20 points
            "engagement_type": "LIKE", # 20 points
        }
        score = compute_activity_score(profile)
        assert score == 70.0

    def test_extract_activity_fields(self):
        fields = extract_activity_fields({
            "connectionsCount": 100,
            "followersCount": 200,
            "isCreator": True,
        })
        assert fields["connection_count"] == 100
        assert fields["follower_count"] == 200
        assert fields["is_creator"] is True
        assert fields["activity_score"] > 0


# ============================================================================
# Filters: Headline pre-filter
# ============================================================================

class TestHeadlinePrefilter:
    def test_is_likely_english_basic(self):
        assert is_likely_english("CEO at Acme Inc")[0] is True

    def test_non_english_cjk(self):
        assert is_likely_english("公司经理")[0] is False

    def test_non_english_cyrillic(self):
        assert is_likely_english("Директор компании")[0] is False

    def test_non_english_indicators(self):
        assert is_likely_english("Geschäftsführer bei Firma GmbH")[0] is False

    def test_prefilter_rejects_students(self):
        engagers = [
            {"reactor": {"headline": "Student at MIT", "name": "A", "profile_url": "url1"}},
            {"reactor": {"headline": "CEO at Acme", "name": "B", "profile_url": "url2"}},
        ]
        filtered, kept, rejected, non_eng = prefilter_engagers_by_headline(engagers)
        assert kept == 1
        assert rejected == 1
        assert len(filtered) == 1
        assert filtered[0]["reactor"]["name"] == "B"

    def test_prefilter_keeps_no_headline(self):
        engagers = [
            {"reactor": {"headline": "", "name": "A", "profile_url": "url1"}},
        ]
        filtered, kept, _, _ = prefilter_engagers_by_headline(engagers)
        assert kept == 1


# ============================================================================
# Filters: Reaction count & post filtering
# ============================================================================

class TestReactionFiltering:
    def test_extract_reaction_count(self):
        assert extract_reaction_count("150+ reactions") == 150
        assert extract_reaction_count("1,234+ reactions") == 1234
        assert extract_reaction_count("") == 0
        assert extract_reaction_count(None) == 0

    def test_filter_posts_by_reactions(self):
        posts = [
            {"followersAmount": "150+ reactions", "url": "url1"},
            {"followersAmount": "10+ reactions", "url": "url2"},
            {"followersAmount": "50+ reactions", "url": "url3"},
        ]
        filtered = filter_posts_by_reactions(posts, min_reactions=50)
        assert len(filtered) == 2  # 150 and 50


# ============================================================================
# Filters: Engagement context
# ============================================================================

class TestEngagementContext:
    def test_aggregate_profile_urls(self):
        engagers = [
            {"reactor": {"profile_url": "url1"}},
            {"reactor": {"profile_url": "url2"}},
            {"reactor": {}},
        ]
        urls = aggregate_profile_urls(engagers)
        assert urls == ["url1", "url2"]

    def test_deduplicate_preserves_order(self):
        urls = ["a", "b", "a", "c", "b"]
        assert deduplicate_profile_urls(urls) == ["a", "b", "c"]

    def test_build_engagement_context(self):
        engagers = [
            {
                "reactor": {"profile_url": "https://linkedin.com/in/test"},
                "reaction_type": "CELEBRATE",
                "_metadata": {"post_url": "https://linkedin.com/posts/xyz"},
            }
        ]
        ctx = build_engagement_context(engagers)
        key = normalize_linkedin_url("https://linkedin.com/in/test")
        assert key in ctx
        assert ctx[key]["engagement_type"] == "CELEBRATE"


# ============================================================================
# Filters: Location
# ============================================================================

class TestLocationFilter:
    def test_filters_by_country(self):
        profiles = [
            {"fullName": "A", "addressCountryOnly": "United States"},
            {"fullName": "B", "addressCountryOnly": "Brazil"},
            {"fullName": "C", "addressCountryOnly": "Canada"},
        ]
        filtered = filter_by_location(profiles, DEFAULT_COUNTRIES)
        assert len(filtered) == 2
        assert {p["fullName"] for p in filtered} == {"A", "C"}


# ============================================================================
# Filters: Profile completeness
# ============================================================================

class TestProfileCompleteness:
    def test_complete_profile(self):
        lead = {"headline": "CEO", "jobTitle": "CEO", "companyName": "Acme", "experiences": [{}]}
        result = is_profile_complete(lead)
        assert result["complete"] is True

    def test_incomplete_no_title_no_company(self):
        lead = {"headline": "--"}
        result = is_profile_complete(lead)
        assert result["complete"] is False

    def test_filter_complete_profiles(self):
        leads = [
            {"headline": "CEO", "jobTitle": "CEO", "companyName": "Acme"},
            {"headline": "--"},
        ]
        complete = filter_complete_profiles(leads)
        assert len(complete) == 1


# ============================================================================
# Prompts
# ============================================================================

class TestPrompts:
    def test_prospect_research_prompt(self):
        prompt = get_prospect_research_prompt(
            name="John Doe", headline="CEO", about="Building things",
            company="Acme", industry="SaaS", experiences="CEO at Acme",
        )
        assert "John Doe" in prompt
        assert "CEO" in prompt
        assert "valid JSON" in prompt

    def test_search_query_prompt(self):
        prompt = get_gift_search_query_prompt(
            icp_description="B2B SaaS founders",
            pain_points=["scaling", "lead gen"],
            buying_signals=["hiring SDRs"],
        )
        assert "B2B SaaS founders" in prompt
        assert "exactly 9" in prompt

    def test_signal_note_prompt(self):
        leads = [{"linkedin_url": "url1", "name": "A", "title": "CEO"}]
        prompt = get_gift_signal_note_prompt("B2B SaaS founders", leads)
        assert "B2B SaaS founders" in prompt
        assert "url1" in prompt


# ============================================================================
# DeepSeek calls (mocked)
# ============================================================================

class TestDeepSeekCalls:
    @pytest.mark.asyncio
    async def test_research_prospect_business(self):
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = json.dumps({
            "icp_description": "B2B SaaS founders",
            "target_titles": ["CEO"],
            "pain_points": ["lead gen"],
            "buying_signals": ["hiring SDRs"],
        })

        with patch("app.services.gift_pipeline.deepseek_calls._get_client") as mock_client:
            mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_completion)

            from app.services.gift_pipeline.deepseek_calls import research_prospect_business
            ct = CostTracker()
            result = await research_prospect_business(
                {"fullName": "John", "headline": "CEO"}, ct,
            )

            assert result["icp_description"] == "B2B SaaS founders"
            assert ct.counts["icp_checks"] == 1

    @pytest.mark.asyncio
    async def test_generate_search_queries(self):
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = json.dumps({
            "queries": ["selling business", "exit planning", "M&A advisor"]
        })

        with patch("app.services.gift_pipeline.deepseek_calls._get_client") as mock_client:
            mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_completion)

            from app.services.gift_pipeline.deepseek_calls import generate_search_queries
            ct = CostTracker()
            result = await generate_search_queries(
                {"icp_description": "test", "pain_points": [], "buying_signals": []},
                ct,
            )

            assert len(result) == 3
            assert all("site:linkedin.com/posts" in q for q in result)
            assert all("after:" in q for q in result)

    @pytest.mark.asyncio
    async def test_qualify_leads(self):
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = json.dumps({
            "match": True, "confidence": "high", "reason": "CEO at SaaS company",
        })

        with patch("app.services.gift_pipeline.deepseek_calls._get_client") as mock_client:
            mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_completion)

            from app.services.gift_pipeline.deepseek_calls import qualify_leads_with_deepseek
            ct = CostTracker()
            leads = [{"fullName": "John", "jobTitle": "CEO", "companyName": "Acme"}]
            result = await qualify_leads_with_deepseek(leads, ct, "B2B SaaS")

            assert len(result) == 1
            assert result[0]["icp_match"] is True
            assert result[0]["icp_confidence"] == "high"

    @pytest.mark.asyncio
    async def test_generate_signal_notes(self):
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = json.dumps([
            {"linkedin_url": "https://linkedin.com/in/test", "signal_note": "Liked post about scaling"},
        ])

        with patch("app.services.gift_pipeline.deepseek_calls._get_client") as mock_client:
            mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_completion)

            from app.services.gift_pipeline.deepseek_calls import generate_signal_notes
            ct = CostTracker()
            leads = [{"linkedinUrl": "https://linkedin.com/in/test", "fullName": "A"}]
            result = await generate_signal_notes(leads, "B2B SaaS", ct)

            assert result[0]["signal_note"] == "Liked post about scaling"


# ============================================================================
# Slack fallback trigger
# ============================================================================

class TestSlackFallbackTrigger:
    """Test that DB returning 0 results triggers the pipeline fallback."""

    @pytest.mark.asyncio
    async def test_no_leads_triggers_pipeline(self):
        """When DB returns 0 leads, _run_gift_pipeline_fallback is called."""
        from unittest.mock import patch, AsyncMock, MagicMock
        import uuid

        prospect_id = uuid.uuid4()

        # Mock the session to return 0 leads
        mock_session = AsyncMock()
        mock_pool_result = MagicMock()
        mock_pool_result.scalar.return_value = 100  # pool_size

        mock_leads_result = MagicMock()
        mock_leads_result.scalars.return_value.all.return_value = []  # no leads

        mock_session.execute = AsyncMock(side_effect=[mock_pool_result, mock_leads_result])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.routers.slack.async_session_factory", return_value=mock_session), \
             patch("app.routers.slack._run_gift_pipeline_fallback", new_callable=AsyncMock) as mock_fallback:

            from app.routers.slack import _process_gift_leads_with_send
            await _process_gift_leads_with_send(
                prospect_id=prospect_id,
                keywords=["video platform"],
                prospect_name="Test User",
            )

            mock_fallback.assert_called_once_with(
                prospect_id=prospect_id,
                prospect_name="Test User",
                keywords=["video platform"],
                pool_size=100,
                auto_send=False,
                icp_description=None,
            )
