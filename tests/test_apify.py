"""Tests for Apify service."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.apify import ApifyError, ApifyService


class TestApifyService:
    """Tests for ApifyService."""

    def test_scrape_calls_correct_actor(self):
        """Should call the LinkedIn profile posts scraper actor."""
        service = ApifyService(api_token="test-token")

        mock_run = {"defaultDatasetId": "ds-123"}
        mock_dataset = MagicMock()
        mock_dataset.iterate_items.return_value = []

        with patch.object(service._client, "actor") as mock_actor, \
             patch.object(service._client, "dataset", return_value=mock_dataset):
            mock_actor.return_value.call.return_value = mock_run

            service.scrape_profile_posts(
                ["https://linkedin.com/in/test-user"],
                max_posts=5,
            )

            mock_actor.assert_called_once_with("RE0MriXnFhR3IgVnJ")
            call_args = mock_actor.return_value.call.call_args
            run_input = call_args[1]["run_input"]
            assert run_input["profileUrls"] == ["https://linkedin.com/in/test-user"]
            assert run_input["maxPosts"] == 5

    def test_scrape_returns_all_results(self):
        """Should return all items from the dataset."""
        service = ApifyService(api_token="test-token")

        mock_run = {"defaultDatasetId": "ds-123"}
        mock_dataset = MagicMock()
        mock_dataset.iterate_items.return_value = [
            {"postUrl": "https://linkedin.com/posts/user_post-1", "text": "Post 1"},
            {"postUrl": "https://linkedin.com/posts/user_post-2", "text": "Post 2"},
        ]

        with patch.object(service._client, "actor") as mock_actor, \
             patch.object(service._client, "dataset", return_value=mock_dataset):
            mock_actor.return_value.call.return_value = mock_run

            results = service.scrape_profile_posts(
                ["https://linkedin.com/in/test-user"],
            )

        assert len(results) == 2
        assert results[0]["text"] == "Post 1"

    def test_scrape_handles_api_error(self):
        """Should raise ApifyError on API failure."""
        service = ApifyService(api_token="test-token")

        with patch.object(service._client, "actor") as mock_actor:
            mock_actor.return_value.call.side_effect = RuntimeError("API down")

            with pytest.raises(ApifyError, match="Apify scrape failed"):
                service.scrape_profile_posts(["https://linkedin.com/in/test"])

    def test_scrape_handles_empty_results(self):
        """Should return empty list when no results."""
        service = ApifyService(api_token="test-token")

        mock_run = {"defaultDatasetId": "ds-123"}
        mock_dataset = MagicMock()
        mock_dataset.iterate_items.return_value = []

        with patch.object(service._client, "actor") as mock_actor, \
             patch.object(service._client, "dataset", return_value=mock_dataset):
            mock_actor.return_value.call.return_value = mock_run

            results = service.scrape_profile_posts(["https://linkedin.com/in/test"])

        assert results == []

    def test_extract_post_url_strips_query_params(self):
        """Should normalize post URLs by removing query params."""
        assert ApifyService.extract_post_url(
            {"postUrl": "https://linkedin.com/posts/john_topic-123?utm_source=share"}
        ) == "https://linkedin.com/posts/john_topic-123"

    def test_extract_post_url_tries_multiple_keys(self):
        """Should try postUrl, url, post_url in order."""
        assert ApifyService.extract_post_url(
            {"url": "https://linkedin.com/posts/test"}
        ) == "https://linkedin.com/posts/test"

        assert ApifyService.extract_post_url(
            {"post_url": "https://linkedin.com/posts/test2"}
        ) == "https://linkedin.com/posts/test2"

    def test_extract_post_url_returns_none_for_empty(self):
        """Should return None when no URL found."""
        assert ApifyService.extract_post_url({}) is None
        assert ApifyService.extract_post_url({"postUrl": ""}) is None

    def test_extract_post_text_tries_multiple_keys(self):
        """Should try text, postText, content, description in order."""
        assert ApifyService.extract_post_text({"text": "Hello"}) == "Hello"
        assert ApifyService.extract_post_text({"postText": "World"}) == "World"
        assert ApifyService.extract_post_text({"content": "Foo"}) == "Foo"
        assert ApifyService.extract_post_text({"description": "Bar"}) == "Bar"
        assert ApifyService.extract_post_text({}) == ""

    def test_scrape_multiple_profiles(self):
        """Should pass all profile URLs in one call."""
        service = ApifyService(api_token="test-token")

        mock_run = {"defaultDatasetId": "ds-123"}
        mock_dataset = MagicMock()
        mock_dataset.iterate_items.return_value = []

        urls = [
            "https://linkedin.com/in/user1",
            "https://linkedin.com/in/user2",
        ]

        with patch.object(service._client, "actor") as mock_actor, \
             patch.object(service._client, "dataset", return_value=mock_dataset):
            mock_actor.return_value.call.return_value = mock_run

            service.scrape_profile_posts(urls, max_posts=3)

            run_input = mock_actor.return_value.call.call_args[1]["run_input"]
            assert run_input["profileUrls"] == urls
            assert run_input["maxPosts"] == 3
