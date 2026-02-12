"""Tests for Apify service."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.services.apify import ApifyError, ApifyService


class TestApifyService:
    """Tests for ApifyService."""

    def test_search_query_format(self):
        """Search query should include site filter, author name, and date."""
        service = ApifyService(api_key="test-key")

        mock_run = {"defaultDatasetId": "ds-123"}
        mock_dataset = MagicMock()
        mock_dataset.iterate_items.return_value = []

        with patch.object(service._client, "actor") as mock_actor, \
             patch.object(service._client, "dataset", return_value=mock_dataset):
            mock_actor.return_value.call.return_value = mock_run

            service.search_linkedin_posts("John Smith", days_back=3)

            # Verify query format
            call_args = mock_actor.return_value.call.call_args
            run_input = call_args[1]["run_input"]
            query = run_input["queries"]

            assert "site:linkedin.com/posts" in query
            assert '"John Smith"' in query
            assert "after:" in query

            # Verify date is correct
            expected_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
            assert expected_date in query

    def test_search_returns_filtered_results(self):
        """Should filter results to only LinkedIn post URLs."""
        service = ApifyService(api_key="test-key")

        mock_run = {"defaultDatasetId": "ds-123"}
        mock_dataset = MagicMock()
        mock_dataset.iterate_items.return_value = [
            {
                "organicResults": [
                    {
                        "url": "https://www.linkedin.com/posts/john-smith_topic-123",
                        "title": "John Smith post",
                        "description": "A great post about...",
                    },
                    {
                        "url": "https://www.linkedin.com/in/john-smith",
                        "title": "John Smith profile",
                        "description": "Profile page",
                    },
                    {
                        "url": "https://www.linkedin.com/feed/update/urn:li:activity:123",
                        "title": "Another post",
                        "description": "Feed update",
                    },
                ]
            }
        ]

        with patch.object(service._client, "actor") as mock_actor, \
             patch.object(service._client, "dataset", return_value=mock_dataset):
            mock_actor.return_value.call.return_value = mock_run

            results = service.search_linkedin_posts("John Smith")

        # Profile URL should be filtered out, posts and feed updates kept
        assert len(results) == 2
        assert results[0]["url"] == "https://www.linkedin.com/posts/john-smith_topic-123"
        assert results[1]["url"] == "https://www.linkedin.com/feed/update/urn:li:activity:123"

    def test_search_normalizes_urls(self):
        """Should strip query params from post URLs."""
        service = ApifyService(api_key="test-key")

        mock_run = {"defaultDatasetId": "ds-123"}
        mock_dataset = MagicMock()
        mock_dataset.iterate_items.return_value = [
            {
                "organicResults": [
                    {
                        "url": "https://www.linkedin.com/posts/john_topic-123?utm_source=share",
                        "title": "Post",
                        "description": "Content",
                    },
                ]
            }
        ]

        with patch.object(service._client, "actor") as mock_actor, \
             patch.object(service._client, "dataset", return_value=mock_dataset):
            mock_actor.return_value.call.return_value = mock_run

            results = service.search_linkedin_posts("John Smith")

        assert results[0]["url"] == "https://www.linkedin.com/posts/john_topic-123"

    def test_search_handles_api_error(self):
        """Should raise ApifyError on API failure."""
        service = ApifyService(api_key="test-key")

        with patch.object(service._client, "actor") as mock_actor:
            mock_actor.return_value.call.side_effect = RuntimeError("API down")

            with pytest.raises(ApifyError, match="Apify search failed"):
                service.search_linkedin_posts("John Smith")

    def test_search_handles_empty_results(self):
        """Should return empty list when no results."""
        service = ApifyService(api_key="test-key")

        mock_run = {"defaultDatasetId": "ds-123"}
        mock_dataset = MagicMock()
        mock_dataset.iterate_items.return_value = []

        with patch.object(service._client, "actor") as mock_actor, \
             patch.object(service._client, "dataset", return_value=mock_dataset):
            mock_actor.return_value.call.return_value = mock_run

            results = service.search_linkedin_posts("John Smith")

        assert results == []

    def test_is_linkedin_post_url(self):
        """Should correctly identify LinkedIn post URLs."""
        assert ApifyService._is_linkedin_post_url(
            "https://www.linkedin.com/posts/john-smith_topic-123"
        )
        assert ApifyService._is_linkedin_post_url(
            "https://www.linkedin.com/feed/update/urn:li:activity:123"
        )
        assert not ApifyService._is_linkedin_post_url(
            "https://www.linkedin.com/in/john-smith"
        )
        assert not ApifyService._is_linkedin_post_url(
            "https://www.google.com"
        )
