"""Tests for the costs API router."""

from datetime import datetime, timezone

import pytest


class TestPostCosts:
    """Tests for POST /api/costs."""

    @pytest.mark.asyncio
    async def test_post_single_cost(self, test_client):
        """Should create a single cost entry."""
        response = await test_client.post(
            "/api/costs",
            json={
                "costs": [
                    {
                        "project": "content_creator",
                        "provider": "anthropic",
                        "operation": "generate_post",
                        "cost_usd": 0.003,
                        "units": 1500,
                        "unit_type": "tokens",
                    }
                ]
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["created"] == 1

    @pytest.mark.asyncio
    async def test_post_batch_costs(self, test_client):
        """Should create multiple cost entries in a single request."""
        response = await test_client.post(
            "/api/costs",
            json={
                "costs": [
                    {
                        "project": "multichannel_outreach",
                        "provider": "apify",
                        "operation": "google_search",
                        "cost_usd": 0.05,
                        "units": 10,
                        "unit_type": "searches",
                    },
                    {
                        "project": "multichannel_outreach",
                        "provider": "deepseek",
                        "operation": "icp_check",
                        "cost_usd": 0.002,
                        "units": 5,
                        "unit_type": "checks",
                    },
                    {
                        "project": "speed_to_lead",
                        "provider": "deepseek",
                        "operation": "reply_draft",
                        "cost_usd": 0.001,
                    },
                ]
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] == 3

    @pytest.mark.asyncio
    async def test_post_cost_with_custom_timestamp(self, test_client):
        """Should accept custom incurred_at timestamp."""
        ts = "2026-02-20T10:00:00+00:00"
        response = await test_client.post(
            "/api/costs",
            json={
                "costs": [
                    {
                        "incurred_at": ts,
                        "project": "content_creator",
                        "provider": "perplexity",
                        "operation": "trend_scout",
                        "cost_usd": 0.01,
                    }
                ]
            },
        )
        assert response.status_code == 200
        assert response.json()["created"] == 1

    @pytest.mark.asyncio
    async def test_post_empty_batch_returns_zero(self, test_client):
        """Should handle empty batch gracefully."""
        response = await test_client.post(
            "/api/costs",
            json={"costs": []},
        )
        assert response.status_code == 200
        assert response.json()["created"] == 0


class TestGetCosts:
    """Tests for GET /api/costs."""

    @pytest.mark.asyncio
    async def test_get_costs_returns_entries(self, test_client):
        """Should return cost entries after posting them."""
        # Seed data
        await test_client.post(
            "/api/costs",
            json={
                "costs": [
                    {
                        "project": "content_creator",
                        "provider": "anthropic",
                        "operation": "generate_post",
                        "cost_usd": 0.005,
                    },
                    {
                        "project": "multichannel_outreach",
                        "provider": "apify",
                        "operation": "profile_scrape",
                        "cost_usd": 0.10,
                    },
                ]
            },
        )

        response = await test_client.get("/api/costs")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert len(data["costs"]) == 2

    @pytest.mark.asyncio
    async def test_get_costs_filter_by_project(self, test_client):
        """Should filter by project."""
        await test_client.post(
            "/api/costs",
            json={
                "costs": [
                    {
                        "project": "content_creator",
                        "provider": "anthropic",
                        "operation": "generate_post",
                        "cost_usd": 0.005,
                    },
                    {
                        "project": "multichannel_outreach",
                        "provider": "apify",
                        "operation": "profile_scrape",
                        "cost_usd": 0.10,
                    },
                ]
            },
        )

        response = await test_client.get("/api/costs?project=content_creator")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["costs"][0]["project"] == "content_creator"

    @pytest.mark.asyncio
    async def test_get_costs_filter_by_provider(self, test_client):
        """Should filter by provider."""
        await test_client.post(
            "/api/costs",
            json={
                "costs": [
                    {
                        "project": "content_creator",
                        "provider": "anthropic",
                        "operation": "generate_post",
                        "cost_usd": 0.005,
                    },
                    {
                        "project": "content_creator",
                        "provider": "perplexity",
                        "operation": "trend_scout",
                        "cost_usd": 0.01,
                    },
                ]
            },
        )

        response = await test_client.get("/api/costs?provider=perplexity")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["costs"][0]["provider"] == "perplexity"

    @pytest.mark.asyncio
    async def test_get_costs_limit_and_offset(self, test_client):
        """Should respect limit and offset."""
        await test_client.post(
            "/api/costs",
            json={
                "costs": [
                    {"project": "p", "provider": "a", "operation": f"op{i}", "cost_usd": 0.001}
                    for i in range(5)
                ]
            },
        )

        response = await test_client.get("/api/costs?limit=2&offset=0")
        assert response.status_code == 200
        assert response.json()["count"] == 2

        response2 = await test_client.get("/api/costs?limit=2&offset=3")
        assert response2.status_code == 200
        assert response2.json()["count"] == 2


class TestCostSummary:
    """Tests for GET /api/costs/summary."""

    @pytest.mark.asyncio
    async def test_summary_by_provider(self, test_client):
        """Should group costs by provider."""
        await test_client.post(
            "/api/costs",
            json={
                "costs": [
                    {
                        "project": "multichannel_outreach",
                        "provider": "apify",
                        "operation": "google_search",
                        "cost_usd": 0.05,
                    },
                    {
                        "project": "multichannel_outreach",
                        "provider": "apify",
                        "operation": "profile_scrape",
                        "cost_usd": 0.10,
                    },
                    {
                        "project": "speed_to_lead",
                        "provider": "deepseek",
                        "operation": "reply_draft",
                        "cost_usd": 0.001,
                    },
                ]
            },
        )

        response = await test_client.get("/api/costs/summary?group_by=provider")
        assert response.status_code == 200
        data = response.json()
        assert data["group_by"] == "provider"
        assert data["grand_total_usd"] == pytest.approx(0.151, abs=0.001)

        groups = {g["name"]: g for g in data["groups"]}
        assert "apify" in groups
        assert "deepseek" in groups
        assert groups["apify"]["total_usd"] == pytest.approx(0.15, abs=0.001)
        assert groups["apify"]["entries"] == 2

    @pytest.mark.asyncio
    async def test_summary_by_project(self, test_client):
        """Should group costs by project."""
        await test_client.post(
            "/api/costs",
            json={
                "costs": [
                    {
                        "project": "content_creator",
                        "provider": "anthropic",
                        "operation": "generate_post",
                        "cost_usd": 0.01,
                    },
                    {
                        "project": "multichannel_outreach",
                        "provider": "apify",
                        "operation": "google_search",
                        "cost_usd": 0.05,
                    },
                ]
            },
        )

        response = await test_client.get("/api/costs/summary?group_by=project")
        assert response.status_code == 200
        data = response.json()
        groups = {g["name"]: g for g in data["groups"]}
        assert "content_creator" in groups
        assert "multichannel_outreach" in groups

    @pytest.mark.asyncio
    async def test_summary_with_project_filter(self, test_client):
        """Should filter summary by project."""
        await test_client.post(
            "/api/costs",
            json={
                "costs": [
                    {
                        "project": "content_creator",
                        "provider": "anthropic",
                        "operation": "generate_post",
                        "cost_usd": 0.01,
                    },
                    {
                        "project": "multichannel_outreach",
                        "provider": "apify",
                        "operation": "google_search",
                        "cost_usd": 0.05,
                    },
                ]
            },
        )

        response = await test_client.get(
            "/api/costs/summary?group_by=provider&project=content_creator"
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["groups"]) == 1
        assert data["groups"][0]["name"] == "anthropic"
