"""Tests for connection request sent & accepted webhooks."""

import json
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import process_connection_sent, process_connection_accepted, _extract_linkedin_url
from app.models import Prospect, ProspectSource


class TestConnectionSentEndpoint:
    """Tests for POST /webhook/heyreach/connection-sent."""

    @pytest.mark.asyncio
    async def test_post_returns_received(self, test_client: AsyncClient):
        """Endpoint should return acknowledgment."""
        response = await test_client.post(
            "/webhook/heyreach/connection-sent",
            json={"lead": {"profile_url": "https://linkedin.com/in/test"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received"
        assert data["event"] == "connection_sent"

    @pytest.mark.asyncio
    async def test_get_returns_verification(self, test_client: AsyncClient):
        """GET should return verification response."""
        response = await test_client.get("/webhook/heyreach/connection-sent")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self, test_client: AsyncClient):
        """Invalid JSON should return error status."""
        response = await test_client.post(
            "/webhook/heyreach/connection-sent",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "error"


class TestConnectionAcceptedEndpoint:
    """Tests for POST /webhook/heyreach/connection-accepted."""

    @pytest.mark.asyncio
    async def test_post_returns_received(self, test_client: AsyncClient):
        """Endpoint should return acknowledgment."""
        response = await test_client.post(
            "/webhook/heyreach/connection-accepted",
            json={"lead": {"profile_url": "https://linkedin.com/in/test"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received"
        assert data["event"] == "connection_accepted"

    @pytest.mark.asyncio
    async def test_get_returns_verification(self, test_client: AsyncClient):
        """GET should return verification response."""
        response = await test_client.get("/webhook/heyreach/connection-accepted")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestProcessConnectionSent:
    """Tests for the process_connection_sent background processor."""

    @pytest.mark.asyncio
    async def test_sets_connection_sent_at(self, test_db_engine):
        """Should set connection_sent_at on matching prospect."""
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False,
        )

        # Seed prospect
        async with session_factory() as session:
            prospect = Prospect(
                linkedin_url="https://linkedin.com/in/testuser",
                full_name="Test User",
                source_type=ProspectSource.COLD_OUTREACH,
            )
            session.add(prospect)
            await session.commit()

        # Patch async_session_factory to use test engine
        import app.main as main_module
        original_factory = main_module.async_session_factory
        main_module.async_session_factory = session_factory

        try:
            result = await process_connection_sent({
                "lead": {"profile_url": "https://linkedin.com/in/testuser"}
            })
            assert result["status"] == "ok"

            # Verify
            async with session_factory() as session:
                res = await session.execute(
                    select(Prospect).where(Prospect.linkedin_url == "https://linkedin.com/in/testuser")
                )
                p = res.scalar_one()
                assert p.connection_sent_at is not None
        finally:
            main_module.async_session_factory = original_factory

    @pytest.mark.asyncio
    async def test_dedup_does_not_overwrite(self, test_db_engine):
        """Calling twice should not overwrite the first timestamp."""
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False,
        )

        original_time = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

        async with session_factory() as session:
            prospect = Prospect(
                linkedin_url="https://linkedin.com/in/dedupuser",
                full_name="Dedup User",
                source_type=ProspectSource.COLD_OUTREACH,
                connection_sent_at=original_time,
            )
            session.add(prospect)
            await session.commit()

        import app.main as main_module
        original_factory = main_module.async_session_factory
        main_module.async_session_factory = session_factory

        try:
            result = await process_connection_sent({
                "lead": {"profile_url": "https://linkedin.com/in/dedupuser"}
            })
            assert result["status"] == "ok"

            async with session_factory() as session:
                res = await session.execute(
                    select(Prospect).where(Prospect.linkedin_url == "https://linkedin.com/in/dedupuser")
                )
                p = res.scalar_one()
                # SQLite strips tzinfo, so compare without it
                assert p.connection_sent_at.replace(tzinfo=None) == original_time.replace(tzinfo=None)
        finally:
            main_module.async_session_factory = original_factory

    @pytest.mark.asyncio
    async def test_unknown_prospect_handled(self, test_db_engine):
        """Unknown prospect should return not_found, not error."""
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False,
        )

        import app.main as main_module
        original_factory = main_module.async_session_factory
        main_module.async_session_factory = session_factory

        try:
            result = await process_connection_sent({
                "lead": {"profile_url": "https://linkedin.com/in/nobody"}
            })
            assert result["status"] == "not_found"
        finally:
            main_module.async_session_factory = original_factory

    @pytest.mark.asyncio
    async def test_no_url_in_payload(self, test_db_engine):
        """Payload without URL should return no_url."""
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False,
        )

        import app.main as main_module
        original_factory = main_module.async_session_factory
        main_module.async_session_factory = session_factory

        try:
            result = await process_connection_sent({"some": "data"})
            assert result["status"] == "no_url"
        finally:
            main_module.async_session_factory = original_factory


class TestProcessConnectionAccepted:
    """Tests for the process_connection_accepted background processor."""

    @pytest.mark.asyncio
    async def test_sets_connection_accepted_at(self, test_db_engine):
        """Should set connection_accepted_at on matching prospect."""
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False,
        )

        async with session_factory() as session:
            prospect = Prospect(
                linkedin_url="https://linkedin.com/in/acceptuser",
                full_name="Accept User",
                source_type=ProspectSource.COLD_OUTREACH,
            )
            session.add(prospect)
            await session.commit()

        import app.main as main_module
        original_factory = main_module.async_session_factory
        main_module.async_session_factory = session_factory

        try:
            result = await process_connection_accepted({
                "lead": {"profile_url": "https://linkedin.com/in/acceptuser"}
            })
            assert result["status"] == "ok"

            async with session_factory() as session:
                res = await session.execute(
                    select(Prospect).where(Prospect.linkedin_url == "https://linkedin.com/in/acceptuser")
                )
                p = res.scalar_one()
                assert p.connection_accepted_at is not None
        finally:
            main_module.async_session_factory = original_factory

    @pytest.mark.asyncio
    async def test_dedup_does_not_overwrite(self, test_db_engine):
        """Calling twice should not overwrite the first timestamp."""
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False,
        )

        original_time = datetime(2026, 1, 20, 10, 0, 0, tzinfo=timezone.utc)

        async with session_factory() as session:
            prospect = Prospect(
                linkedin_url="https://linkedin.com/in/acceptdedup",
                full_name="Accept Dedup",
                source_type=ProspectSource.COLD_OUTREACH,
                connection_accepted_at=original_time,
            )
            session.add(prospect)
            await session.commit()

        import app.main as main_module
        original_factory = main_module.async_session_factory
        main_module.async_session_factory = session_factory

        try:
            result = await process_connection_accepted({
                "lead": {"profile_url": "https://linkedin.com/in/acceptdedup"}
            })
            assert result["status"] == "ok"

            async with session_factory() as session:
                res = await session.execute(
                    select(Prospect).where(Prospect.linkedin_url == "https://linkedin.com/in/acceptdedup")
                )
                p = res.scalar_one()
                # SQLite strips tzinfo, so compare without it
                assert p.connection_accepted_at.replace(tzinfo=None) == original_time.replace(tzinfo=None)
        finally:
            main_module.async_session_factory = original_factory


class TestExtractLinkedinUrl:
    """Tests for the _extract_linkedin_url helper."""

    def test_extracts_from_lead_profile_url(self):
        assert _extract_linkedin_url({"lead": {"profile_url": "https://linkedin.com/in/foo"}}) == "https://linkedin.com/in/foo"

    def test_extracts_from_lead_profileUrl(self):
        assert _extract_linkedin_url({"lead": {"profileUrl": "https://linkedin.com/in/bar"}}) == "https://linkedin.com/in/bar"

    def test_extracts_from_flat_linkedin_profile_url(self):
        assert _extract_linkedin_url({"linkedin_profile_url": "https://linkedin.com/in/baz"}) == "https://linkedin.com/in/baz"

    def test_extracts_from_flat_profile_url(self):
        assert _extract_linkedin_url({"profile_url": "https://linkedin.com/in/qux"}) == "https://linkedin.com/in/qux"

    def test_returns_none_for_empty(self):
        assert _extract_linkedin_url({}) is None

    def test_returns_none_for_no_url(self):
        assert _extract_linkedin_url({"lead": {"name": "Test"}}) is None


class TestFunnelWithConnectionTracking:
    """Tests for funnel summary including connection tracking fields."""

    @pytest.mark.asyncio
    async def test_funnel_includes_connection_counts(self, test_client: AsyncClient, test_db_engine):
        """Funnel endpoint should include connection_requests_sent and connections_accepted."""
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False,
        )

        async with session_factory() as session:
            now = datetime.now(timezone.utc)

            p1 = Prospect(
                linkedin_url="https://linkedin.com/in/funnel1",
                full_name="Funnel One",
                source_type=ProspectSource.COLD_OUTREACH,
                connection_sent_at=now,
                connection_accepted_at=now,
                heyreach_uploaded_at=now,
            )
            p2 = Prospect(
                linkedin_url="https://linkedin.com/in/funnel2",
                full_name="Funnel Two",
                source_type=ProspectSource.COLD_OUTREACH,
                connection_sent_at=now,
            )
            p3 = Prospect(
                linkedin_url="https://linkedin.com/in/funnel3",
                full_name="Funnel Three",
                source_type=ProspectSource.COLD_OUTREACH,
                heyreach_uploaded_at=now,
            )
            session.add_all([p1, p2, p3])
            await session.commit()

        response = await test_client.get("/api/metrics/funnel")
        assert response.status_code == 200
        data = response.json()

        funnel = data["funnel"]
        assert funnel["connection_requests_sent"] == 2
        assert funnel["connections_accepted"] == 1
        assert funnel["initial_msgs_sent"] == 2

        rates = data["conversion_rates"]
        assert "accept_rate" in rates
        assert rates["accept_rate"] == "50.0%"
