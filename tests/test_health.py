"""Tests for health check endpoint."""

import pytest
from httpx import AsyncClient


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_ok(self, test_client: AsyncClient):
        """Health endpoint should return 200 OK."""
        response = await test_client.get("/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_returns_status(self, test_client: AsyncClient):
        """Health endpoint should return healthy status."""
        response = await test_client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_returns_environment(self, test_client: AsyncClient):
        """Health endpoint should return the current environment."""
        response = await test_client.get("/health")
        data = response.json()
        assert "environment" in data
