"""Tests for engagement API router."""

import uuid

import pytest

from app.models import (
    EngagementPost,
    EngagementPostStatus,
    WatchedProfile,
    WatchedProfileCategory,
)


class TestWatchlistEndpoints:
    """Tests for watchlist CRUD endpoints."""

    @pytest.mark.asyncio
    async def test_add_profile_to_watchlist(self, test_client):
        """Should create a new watched profile."""
        response = await test_client.post(
            "/api/engagement/watchlist",
            json={
                "linkedin_url": "https://www.linkedin.com/in/test-user",
                "name": "Test User",
                "headline": "CEO at TestCo",
                "category": "prospect",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["name"] == "Test User"
        assert data["category"] == "prospect"

    @pytest.mark.asyncio
    async def test_add_duplicate_returns_409(self, test_client):
        """Should return 409 when adding a duplicate profile."""
        payload = {
            "linkedin_url": "https://www.linkedin.com/in/dupe-user",
            "name": "Dupe User",
            "category": "influencer",
        }

        # First add
        response = await test_client.post("/api/engagement/watchlist", json=payload)
        assert response.status_code == 201

        # Duplicate
        response = await test_client.post("/api/engagement/watchlist", json=payload)
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_normalizes_linkedin_url(self, test_client):
        """Should normalize URL (lowercase, strip trailing slash, remove query params)."""
        response = await test_client.post(
            "/api/engagement/watchlist",
            json={
                "linkedin_url": "HTTPS://WWW.LINKEDIN.COM/IN/TEST-NORM/?ref=share",
                "name": "Norm User",
            },
        )

        assert response.status_code == 201

        # Check that the URL was normalized in the listing
        list_resp = await test_client.get("/api/engagement/watchlist?active_only=false")
        profiles = list_resp.json()["profiles"]
        norm_profiles = [p for p in profiles if p["name"] == "Norm User"]
        assert len(norm_profiles) == 1
        assert norm_profiles[0]["linkedin_url"] == "https://www.linkedin.com/in/test-norm"

    @pytest.mark.asyncio
    async def test_list_watchlist(self, test_client):
        """Should list all active profiles."""
        # Add profiles
        await test_client.post(
            "/api/engagement/watchlist",
            json={
                "linkedin_url": "https://linkedin.com/in/list-user-1",
                "name": "List User 1",
                "category": "prospect",
            },
        )
        await test_client.post(
            "/api/engagement/watchlist",
            json={
                "linkedin_url": "https://linkedin.com/in/list-user-2",
                "name": "List User 2",
                "category": "influencer",
            },
        )

        response = await test_client.get("/api/engagement/watchlist")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 2

    @pytest.mark.asyncio
    async def test_list_watchlist_filter_by_category(self, test_client):
        """Should filter by category."""
        await test_client.post(
            "/api/engagement/watchlist",
            json={
                "linkedin_url": "https://linkedin.com/in/cat-prospect",
                "name": "Prospect User",
                "category": "prospect",
            },
        )
        await test_client.post(
            "/api/engagement/watchlist",
            json={
                "linkedin_url": "https://linkedin.com/in/cat-influencer",
                "name": "Influencer User",
                "category": "influencer",
            },
        )

        response = await test_client.get(
            "/api/engagement/watchlist?category=influencer"
        )
        assert response.status_code == 200
        data = response.json()
        for p in data["profiles"]:
            assert p["category"] == "influencer"

    @pytest.mark.asyncio
    async def test_update_profile(self, test_client):
        """Should update a profile's fields."""
        # Create
        create_resp = await test_client.post(
            "/api/engagement/watchlist",
            json={
                "linkedin_url": "https://linkedin.com/in/update-me",
                "name": "Original Name",
                "category": "prospect",
            },
        )
        profile_id = create_resp.json()["id"]

        # Update
        response = await test_client.patch(
            f"/api/engagement/watchlist/{profile_id}",
            json={"name": "Updated Name", "category": "influencer"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "updated"

    @pytest.mark.asyncio
    async def test_delete_profile_soft_deletes(self, test_client):
        """Should soft-delete by setting is_active=false."""
        create_resp = await test_client.post(
            "/api/engagement/watchlist",
            json={
                "linkedin_url": "https://linkedin.com/in/delete-me",
                "name": "Delete Me",
            },
        )
        profile_id = create_resp.json()["id"]

        response = await test_client.delete(
            f"/api/engagement/watchlist/{profile_id}"
        )
        assert response.status_code == 200
        assert response.json()["status"] == "deactivated"

        # Should not appear in active-only list
        list_resp = await test_client.get("/api/engagement/watchlist?active_only=true")
        ids = [p["id"] for p in list_resp.json()["profiles"]]
        assert profile_id not in ids

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, test_client):
        """Should return 404 for non-existent profile."""
        fake_id = str(uuid.uuid4())
        response = await test_client.delete(
            f"/api/engagement/watchlist/{fake_id}"
        )
        assert response.status_code == 404


class TestCheckNowEndpoint:
    """Tests for manual trigger endpoint."""

    @pytest.mark.asyncio
    async def test_check_now_starts_background_task(self, test_client):
        """Should return immediately with started status."""
        response = await test_client.post("/api/engagement/check-now")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"


class TestPostsEndpoint:
    """Tests for engagement posts listing."""

    @pytest.mark.asyncio
    async def test_list_posts_empty(self, test_client):
        """Should return empty list when no posts."""
        response = await test_client.get("/api/engagement/posts")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["posts"] == []
