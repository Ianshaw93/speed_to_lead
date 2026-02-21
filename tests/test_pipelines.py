"""Tests for pipelines API router."""

from unittest.mock import patch, MagicMock

import pytest


class TestGiftLeadsEndpoint:
    """Tests for POST /api/pipelines/gift-leads."""

    @pytest.mark.asyncio
    async def test_valid_request_returns_started(self, test_client):
        """Should return 200 with started status for valid request."""
        with patch("app.routers.pipelines.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            response = await test_client.post(
                "/api/pipelines/gift-leads",
                json={"prospect_url": "https://linkedin.com/in/johndoe"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert "johndoe" in data["message"]

    @pytest.mark.asyncio
    async def test_missing_prospect_url_returns_422(self, test_client):
        """Should return 422 when prospect_url is missing."""
        response = await test_client.post(
            "/api/pipelines/gift-leads",
            json={},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_skip_research_without_icp_returns_400(self, test_client):
        """Should return 400 when skip_research is true but icp is not provided."""
        response = await test_client.post(
            "/api/pipelines/gift-leads",
            json={
                "prospect_url": "https://linkedin.com/in/johndoe",
                "skip_research": True,
            },
        )
        assert response.status_code == 400
        assert "icp" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_skip_research_with_icp_succeeds(self, test_client):
        """Should succeed when skip_research is true and icp is provided."""
        with patch("app.routers.pipelines.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            response = await test_client.post(
                "/api/pipelines/gift-leads",
                json={
                    "prospect_url": "https://linkedin.com/in/johndoe",
                    "skip_research": True,
                    "icp": "B2B SaaS founders",
                },
            )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_subprocess_spawned_with_correct_args(self, test_client):
        """Should spawn subprocess with correct CLI arguments."""
        with patch("app.routers.pipelines.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            await test_client.post(
                "/api/pipelines/gift-leads",
                json={
                    "prospect_url": "https://linkedin.com/in/johndoe",
                    "icp": "B2B SaaS founders",
                    "pain_points": "lead gen",
                    "days_back": 7,
                    "min_reactions": 100,
                    "min_leads": 5,
                    "max_leads": 15,
                    "dry_run": True,
                    "skip_research": True,
                },
            )

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "--prospect-url" in cmd
        assert "https://linkedin.com/in/johndoe" in cmd
        assert "--icp" in cmd
        assert "B2B SaaS founders" in cmd
        assert "--pain-points" in cmd
        assert "lead gen" in cmd
        assert "--days-back" in cmd
        assert "7" in cmd
        assert "--min-reactions" in cmd
        assert "100" in cmd
        assert "--min-leads" in cmd
        assert "5" in cmd
        assert "--max-leads" in cmd
        assert "15" in cmd
        assert "--dry-run" in cmd
        assert "--skip-research" in cmd

    @pytest.mark.asyncio
    async def test_missing_script_returns_500(self, test_client):
        """Should return 500 when the gift leads script is not found."""
        with patch("app.routers.pipelines.GIFT_LEADS_SCRIPT", "/nonexistent/path/script.py"):
            with patch("app.routers.pipelines.Path") as mock_path_cls:
                mock_path_instance = MagicMock()
                mock_path_instance.exists.return_value = False
                mock_path_cls.return_value = mock_path_instance
                response = await test_client.post(
                    "/api/pipelines/gift-leads",
                    json={"prospect_url": "https://linkedin.com/in/johndoe"},
                )

        assert response.status_code == 500
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_optional_params_have_defaults(self, test_client):
        """Should use default values when optional params are omitted."""
        with patch("app.routers.pipelines.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            await test_client.post(
                "/api/pipelines/gift-leads",
                json={"prospect_url": "https://linkedin.com/in/johndoe"},
            )

        cmd = mock_popen.call_args[0][0]
        # Defaults should be applied
        assert "--days-back" in cmd
        assert "14" in cmd
        assert "--min-reactions" in cmd
        assert "50" in cmd
        assert "--min-leads" in cmd
        assert "10" in cmd
        assert "--max-leads" in cmd
        assert "25" in cmd
        # Boolean flags should NOT be present by default
        assert "--dry-run" not in cmd
        assert "--skip-research" not in cmd
