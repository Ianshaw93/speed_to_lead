"""Tests for the clients API router."""

import pytest


class TestPostClient:
    """Tests for POST /api/clients."""

    @pytest.mark.asyncio
    async def test_create_client_minimal(self, test_client):
        """Should create a client with just a name."""
        response = await test_client.post(
            "/api/clients",
            json={"name": "Mandy Smith"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Mandy Smith"
        assert data["status"] == "active"
        assert data["id"] is not None

    @pytest.mark.asyncio
    async def test_create_client_full(self, test_client):
        """Should create a client with all fields."""
        response = await test_client.post(
            "/api/clients",
            json={
                "name": "Mandy Smith",
                "email": "mandy@example.com",
                "linkedin_url": "https://linkedin.com/in/mandysmith",
                "company": "Acme Coaching",
                "status": "ex_client",
                "case_study_data": {
                    "before": "3 years without any LinkedIn sales",
                    "result": "Closed 25k through a sale",
                    "ltv": 100000,
                    "revenue_closed": 25000,
                    "offer": "Backend offer with 100k LTV",
                },
                "notes": "Very active on LinkedIn recently",
                "started_at": "2025-01-15",
                "ended_at": "2025-09-01",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Mandy Smith"
        assert data["email"] == "mandy@example.com"
        assert data["status"] == "ex_client"
        assert data["case_study_data"]["ltv"] == 100000
        assert data["started_at"] == "2025-01-15"

    @pytest.mark.asyncio
    async def test_create_client_missing_name_returns_422(self, test_client):
        """Name is required."""
        response = await test_client.post(
            "/api/clients",
            json={"email": "someone@example.com"},
        )
        assert response.status_code == 422


class TestGetClients:
    """Tests for GET /api/clients."""

    @pytest.mark.asyncio
    async def test_list_clients_empty(self, test_client):
        """Should return empty list when no clients exist."""
        response = await test_client.get("/api/clients")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_clients_returns_all(self, test_client):
        """Should return all clients."""
        await test_client.post("/api/clients", json={"name": "Client A"})
        await test_client.post("/api/clients", json={"name": "Client B"})

        response = await test_client.get("/api/clients")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_list_clients_filter_by_status(self, test_client):
        """Should filter clients by status."""
        await test_client.post(
            "/api/clients", json={"name": "Active Client", "status": "active"}
        )
        await test_client.post(
            "/api/clients", json={"name": "Ex Client", "status": "ex_client"}
        )

        response = await test_client.get("/api/clients?status=ex_client")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Ex Client"


class TestGetClient:
    """Tests for GET /api/clients/{client_id}."""

    @pytest.mark.asyncio
    async def test_get_client_by_id(self, test_client):
        """Should return a single client by ID."""
        create_resp = await test_client.post(
            "/api/clients",
            json={"name": "Mandy Smith", "company": "Acme Coaching"},
        )
        client_id = create_resp.json()["id"]

        response = await test_client.get(f"/api/clients/{client_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Mandy Smith"
        assert data["company"] == "Acme Coaching"

    @pytest.mark.asyncio
    async def test_get_client_not_found(self, test_client):
        """Should return 404 for non-existent client."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = await test_client.get(f"/api/clients/{fake_id}")
        assert response.status_code == 404


class TestPatchClient:
    """Tests for PATCH /api/clients/{client_id}."""

    @pytest.mark.asyncio
    async def test_update_client_notes(self, test_client):
        """Should update notes on an existing client."""
        create_resp = await test_client.post(
            "/api/clients", json={"name": "Mandy Smith"}
        )
        client_id = create_resp.json()["id"]

        response = await test_client.patch(
            f"/api/clients/{client_id}",
            json={"notes": "Just posted on LinkedIn about new offer"},
        )
        assert response.status_code == 200
        assert response.json()["notes"] == "Just posted on LinkedIn about new offer"

    @pytest.mark.asyncio
    async def test_update_client_status(self, test_client):
        """Should update client status."""
        create_resp = await test_client.post(
            "/api/clients", json={"name": "Mandy Smith", "status": "active"}
        )
        client_id = create_resp.json()["id"]

        response = await test_client.patch(
            f"/api/clients/{client_id}",
            json={"status": "ex_client"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ex_client"

    @pytest.mark.asyncio
    async def test_update_client_case_study(self, test_client):
        """Should update case study data."""
        create_resp = await test_client.post(
            "/api/clients", json={"name": "Mandy Smith"}
        )
        client_id = create_resp.json()["id"]

        case_study = {
            "before": "No LinkedIn presence",
            "result": "Closed 25k deal",
            "ltv": 100000,
        }
        response = await test_client.patch(
            f"/api/clients/{client_id}",
            json={"case_study_data": case_study},
        )
        assert response.status_code == 200
        assert response.json()["case_study_data"]["ltv"] == 100000

    @pytest.mark.asyncio
    async def test_update_client_not_found(self, test_client):
        """Should return 404 for non-existent client."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = await test_client.patch(
            f"/api/clients/{fake_id}",
            json={"notes": "test"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_preserves_unset_fields(self, test_client):
        """PATCH should only update provided fields, not clear others."""
        create_resp = await test_client.post(
            "/api/clients",
            json={
                "name": "Mandy Smith",
                "email": "mandy@example.com",
                "company": "Acme Coaching",
            },
        )
        client_id = create_resp.json()["id"]

        # Update only notes
        await test_client.patch(
            f"/api/clients/{client_id}",
            json={"notes": "New note"},
        )

        # Verify other fields are preserved
        get_resp = await test_client.get(f"/api/clients/{client_id}")
        data = get_resp.json()
        assert data["email"] == "mandy@example.com"
        assert data["company"] == "Acme Coaching"
        assert data["notes"] == "New note"
