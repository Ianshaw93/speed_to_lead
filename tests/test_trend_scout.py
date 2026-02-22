"""Tests for the trend scout feature."""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# content_db module tests
# ---------------------------------------------------------------------------


class TestContentDb:
    """Tests for the content DB session and TrendingTopic model."""

    def test_trending_topic_model_fields(self):
        """TrendingTopic model should have all required columns."""
        from app.services.content_db import TrendingTopic

        assert TrendingTopic.__tablename__ == "trending_topics"
        cols = {c.name for c in TrendingTopic.__table__.columns}
        expected = {
            "id", "topic", "summary", "source_urls", "relevance_score",
            "content_angles", "search_query", "batch_id", "status",
            "source_platform", "created_at", "updated_at", "notes",
        }
        assert expected.issubset(cols)

    @patch("app.services.content_db._get_content_engine")
    def test_save_trending_topic(self, mock_engine):
        """save_trending_topic should insert a row and return a dict."""
        from app.services.content_db import save_trending_topic

        # Set up a mock session
        mock_session = MagicMock()
        mock_engine.return_value = MagicMock()

        with patch("app.services.content_db._get_content_session") as mock_get_sess:
            mock_get_sess.return_value = mock_session

            result = save_trending_topic(
                topic="AI for coaches",
                summary="Big trend in coaching",
                source_urls=["https://example.com"],
                relevance_score=8,
                content_angles=["Share your take"],
                batch_id="abc123",
                source_platform="reddit",
            )

        assert result["topic"] == "AI for coaches"
        assert result["relevance_score"] == 8
        assert result["batch_id"] == "abc123"
        assert result["status"] == "new"
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()


# ---------------------------------------------------------------------------
# trend_scout pipeline tests
# ---------------------------------------------------------------------------


class TestTrendScoutPipeline:
    """Tests for the async trend scout pipeline."""

    @pytest.mark.asyncio
    async def test_search_perplexity_single(self):
        """Should call Perplexity API and return parsed result."""
        from app.services.trend_scout import _search_perplexity

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Some trending topics..."}}],
            "citations": ["https://reddit.com/r/startups/1234"],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("app.services.trend_scout.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _search_perplexity("test query", "reddit", mock_client)

        assert result["content"] == "Some trending topics..."
        assert result["platform"] == "reddit"
        assert "https://reddit.com/r/startups/1234" in result["citations"]

    @pytest.mark.asyncio
    async def test_score_and_extract_topics(self):
        """Should call Claude and return scored topics."""
        from app.services.trend_scout import _score_and_extract_topics

        mock_topics = [
            {
                "topic": "AI Automation",
                "summary": "Coaches adopting AI",
                "source_urls": [],
                "relevance_score": 9,
                "content_angles": ["Share your AI story"],
                "source_platform": "reddit",
            }
        ]

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='[{"topic": "AI Automation", "summary": "Coaches adopting AI", "source_urls": [], "relevance_score": 9, "content_angles": ["Share your AI story"], "source_platform": "reddit"}]')]

        with patch("app.services.trend_scout.anthropic.AsyncAnthropic") as MockAnthropic:
            mock_client = AsyncMock()
            mock_client.messages.create.return_value = mock_response
            MockAnthropic.return_value = mock_client

            search_results = [
                {"content": "AI trends for coaches", "citations": [], "query": "test", "platform": "reddit"},
            ]
            result = await _score_and_extract_topics(search_results)

        assert len(result) == 1
        assert result[0]["topic"] == "AI Automation"
        assert result[0]["relevance_score"] == 9

    @pytest.mark.asyncio
    async def test_run_trend_scout_task_full_pipeline(self):
        """Full pipeline should search, score, save, and send Slack report."""
        from app.services.trend_scout import run_trend_scout_task

        mock_search_result = {
            "content": "Trending topic content",
            "citations": ["https://example.com"],
            "query": "test query",
            "platform": "reddit",
        }

        mock_scored = [
            {
                "topic": "AI for Coaches",
                "summary": "Big trend",
                "source_urls": ["https://example.com"],
                "relevance_score": 8,
                "content_angles": ["Share your take"],
                "source_platform": "reddit",
            }
        ]

        with (
            patch("app.services.trend_scout._search_perplexity", new_callable=AsyncMock, return_value=mock_search_result),
            patch("app.services.trend_scout._score_and_extract_topics", new_callable=AsyncMock, return_value=mock_scored),
            patch("app.services.trend_scout.save_trending_topic") as mock_save,
            patch("app.services.slack.get_slack_bot") as mock_slack,
        ):
            mock_save.return_value = {**mock_scored[0], "id": "test-id", "status": "new"}
            mock_bot = AsyncMock()
            mock_slack.return_value = mock_bot

            result = await run_trend_scout_task()

        assert result["topics_saved"] == 1
        assert result["topics_found"] == 1
        assert "batch_id" in result
        mock_save.assert_called_once()
        mock_bot.send_trend_scout_report.assert_called_once()


# ---------------------------------------------------------------------------
# Scheduler registration tests
# ---------------------------------------------------------------------------


class TestSchedulerRegistration:
    """Tests for trend scout scheduler registration."""

    def test_trend_scout_job_registered(self):
        """Scheduler should register the trend_scout_weekly job."""
        with patch("app.services.scheduler.AsyncIOScheduler") as MockScheduler:
            mock_scheduler = MagicMock()
            mock_scheduler.running = False
            MockScheduler.return_value = mock_scheduler

            from app.services.scheduler import SchedulerService
            service = SchedulerService()
            service.start()

            # Collect all job IDs from add_job calls
            job_ids = [
                call.kwargs.get("id")
                for call in mock_scheduler.add_job.call_args_list
            ]
            assert "trend_scout_weekly" in job_ids


# ---------------------------------------------------------------------------
# Manual trigger endpoint tests
# ---------------------------------------------------------------------------


class TestTrendScoutEndpoint:
    """Tests for the POST /api/trend-scout/run endpoint."""

    @pytest.mark.asyncio
    async def test_trigger_returns_processing(self):
        """POST /api/trend-scout/run should return 200 with processing status."""
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/trend-scout/run")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processing"


# ---------------------------------------------------------------------------
# Slack report tests
# ---------------------------------------------------------------------------


class TestTrendScoutSlackReport:
    """Tests for the Slack trend scout report."""

    def test_build_trend_scout_report_blocks(self):
        """Should build Block Kit blocks with topic summary."""
        from app.services.slack import build_trend_scout_report_blocks

        result = {
            "batch_id": "abc123",
            "topics_found": 5,
            "topics_saved": 3,
            "topics": [
                {"topic": "AI Coaching", "relevance_score": 9, "source_platform": "reddit"},
                {"topic": "Personal Branding", "relevance_score": 8, "source_platform": "linkedin"},
                {"topic": "Client Acquisition", "relevance_score": 7, "source_platform": "web"},
            ],
        }

        blocks = build_trend_scout_report_blocks(result)
        assert len(blocks) > 0
        # Should have header
        assert blocks[0]["type"] == "header"
        # Should contain topic list somewhere in the text
        text_content = str(blocks)
        assert "AI Coaching" in text_content
        assert "abc123" in text_content

    @pytest.mark.asyncio
    async def test_send_trend_scout_report(self):
        """SlackBot.send_trend_scout_report should post to metrics channel."""
        from app.services.slack import SlackBot

        bot = SlackBot(
            bot_token="xoxb-test",
            channel_id="C_TEST",
            metrics_channel_id="C_METRICS",
        )
        bot._client = AsyncMock()
        bot._client.chat_postMessage.return_value = {"ts": "123.456"}

        result = {
            "batch_id": "abc123",
            "topics_found": 5,
            "topics_saved": 3,
            "topics": [
                {"topic": "AI Coaching", "relevance_score": 9, "source_platform": "reddit"},
            ],
        }

        ts = await bot.send_trend_scout_report(result)
        assert ts == "123.456"
        bot._client.chat_postMessage.assert_called_once()
        call_kwargs = bot._client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C_METRICS"
