"""Tests for the automated buying signal outreach pipeline."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Prospect, ProspectSource


def _seed_prospect(session, **overrides):
    """Create a buying signal prospect for testing."""
    defaults = {
        "linkedin_url": "https://linkedin.com/in/testuser",
        "full_name": "Test User",
        "first_name": "Test",
        "last_name": "User",
        "job_title": "CEO",
        "company_name": "TestCo",
        "company_industry": "SaaS",
        "location": "New York, US",
        "headline": "CEO @ TestCo | B2B SaaS",
        "source_type": ProspectSource.BUYING_SIGNAL,
        "personalized_message": None,
    }
    defaults.update(overrides)
    prospect = Prospect(**defaults)
    session.add(prospect)
    return prospect


class TestGetUnprocessedBuyingSignals:
    """Tests for querying unprocessed buying signal prospects."""

    @pytest.mark.asyncio
    async def test_returns_unprocessed_only(self, test_db_session: AsyncSession):
        """Should return only BUYING_SIGNAL prospects without personalized_message."""
        # Unprocessed buying signal - should be returned
        _seed_prospect(test_db_session, linkedin_url="https://linkedin.com/in/unprocessed1")
        # Processed buying signal - should NOT be returned
        _seed_prospect(
            test_db_session,
            linkedin_url="https://linkedin.com/in/processed1",
            personalized_message="Hey there",
        )
        # Different source type - should NOT be returned
        _seed_prospect(
            test_db_session,
            linkedin_url="https://linkedin.com/in/cold1",
            source_type=ProspectSource.COLD_OUTREACH,
        )
        await test_db_session.commit()

        from app.services.buying_signal_outreach import get_unprocessed_buying_signals
        results = await get_unprocessed_buying_signals(test_db_session)

        assert len(results) == 1
        assert results[0].linkedin_url == "https://linkedin.com/in/unprocessed1"

    @pytest.mark.asyncio
    async def test_returns_empty_when_none(self, test_db_session: AsyncSession):
        """Should return empty list when no unprocessed prospects exist."""
        from app.services.buying_signal_outreach import get_unprocessed_buying_signals
        results = await get_unprocessed_buying_signals(test_db_session)
        assert results == []


class TestBuildPrompt:
    """Tests for prompt generation."""

    def test_builds_5_line_prompt_with_location(self):
        from app.services.buying_signal_outreach import _build_prompt
        prompt = _build_prompt(
            first_name="Juan",
            company_name="Axolop",
            title="CEO",
            industry="SaaS",
            location="Tampa",
            skip_location=False,
            headline="CEO @ Axolop",
            about="We help B2B founders",
        )
        assert "Juan" in prompt
        assert "Axolop" in prompt
        assert "5 lines" in prompt
        assert "Location Hook" in prompt

    def test_builds_4_line_prompt_without_location(self):
        from app.services.buying_signal_outreach import _build_prompt
        prompt = _build_prompt(
            first_name="Juan",
            company_name="Axolop",
            title="CEO",
            industry="SaaS",
            location="Tampa",
            skip_location=True,
        )
        assert "4 lines" in prompt
        assert "Do NOT include a location hook" in prompt

    def test_includes_top5_signal(self):
        from app.services.buying_signal_outreach import _build_prompt
        prompt = _build_prompt(
            first_name="Juan",
            company_name="Axolop",
            title="CEO",
            industry="SaaS",
            location="Tampa",
            skip_location=False,
        )
        assert "top 5% most active" in prompt


class TestGenerateMessage:
    """Tests for DeepSeek message generation."""

    @pytest.mark.asyncio
    async def test_generates_message(self, test_db_session: AsyncSession):
        """Should call DeepSeek and return cleaned message."""
        _seed_prospect(test_db_session)
        await test_db_session.commit()

        result = await test_db_session.execute(select(Prospect))
        prospect = result.scalar_one()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hey Test\n\nGreat message here"}}]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            from app.services.buying_signal_outreach import generate_message
            msg = await generate_message(prospect, None, False)

        assert msg is not None
        assert "Hey Test" in msg

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, test_db_session: AsyncSession):
        """Should return None if DeepSeek call fails."""
        _seed_prospect(test_db_session)
        await test_db_session.commit()

        result = await test_db_session.execute(select(Prospect))
        prospect = result.scalar_one()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=Exception("API down"))
            mock_client_cls.return_value = mock_client

            from app.services.buying_signal_outreach import generate_message
            msg = await generate_message(prospect, None, False)

        assert msg is None


class TestUploadToHeyreach:
    """Tests for HeyReach upload."""

    @pytest.mark.asyncio
    async def test_uploads_leads(self):
        """Should call HeyReach add_leads_to_list with correct format."""
        mock_client = MagicMock()
        mock_client.add_leads_to_list = AsyncMock(return_value={"addedCount": 2})

        with patch("app.services.heyreach.get_heyreach_client", return_value=mock_client):
            from app.services.buying_signal_outreach import upload_to_heyreach
            result = await upload_to_heyreach(
                [
                    {
                        "linkedin_url": "https://linkedin.com/in/user1",
                        "first_name": "User",
                        "last_name": "One",
                        "company_name": "Co1",
                        "job_title": "CEO",
                        "personalized_message": "Hey User",
                    },
                    {
                        "linkedin_url": "https://linkedin.com/in/user2",
                        "first_name": "User",
                        "last_name": "Two",
                        "company_name": "Co2",
                        "job_title": "CTO",
                        "personalized_message": "Hey User2",
                    },
                ],
                list_id=480247,
            )

        assert result == 2
        mock_client.add_leads_to_list.assert_called_once()
        call_args = mock_client.add_leads_to_list.call_args
        assert call_args[0][0] == 480247
        assert len(call_args[0][1]) == 2
        # Check custom fields are set
        lead = call_args[0][1][0]
        assert lead["custom_fields"]["personalized_message"] == "Hey User"

    @pytest.mark.asyncio
    async def test_skips_leads_without_message(self):
        """Should skip leads that have no personalized_message."""
        mock_client = MagicMock()
        mock_client.add_leads_to_list = AsyncMock(return_value={"addedCount": 0})

        with patch("app.services.heyreach.get_heyreach_client", return_value=mock_client):
            from app.services.buying_signal_outreach import upload_to_heyreach
            result = await upload_to_heyreach(
                [{"linkedin_url": "https://linkedin.com/in/user1", "personalized_message": None}],
                list_id=480247,
            )

        assert result == 0
        mock_client.add_leads_to_list.assert_not_called()


class TestProcessBuyingSignalBatch:
    """Tests for the full batch orchestrator."""

    @pytest.mark.asyncio
    async def test_full_flow(self, test_db_engine):
        """Should query -> scrape -> generate -> upload -> update DB."""
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False,
        )

        # Seed unprocessed prospects
        async with session_factory() as session:
            session.add(Prospect(
                linkedin_url="https://linkedin.com/in/batch1",
                full_name="Batch One",
                first_name="Batch",
                last_name="One",
                job_title="CEO",
                company_name="BatchCo",
                company_industry="Tech",
                location="San Francisco, US",
                source_type=ProspectSource.BUYING_SIGNAL,
            ))
            session.add(Prospect(
                linkedin_url="https://linkedin.com/in/batch2",
                full_name="Batch Two",
                first_name="Batch",
                last_name="Two",
                job_title="CTO",
                company_name="BatchCo2",
                company_industry="Finance",
                location="London, UK",
                source_type=ProspectSource.BUYING_SIGNAL,
            ))
            await session.commit()

        # Mock all external calls
        mock_profiles = {
            "https://linkedin.com/in/batch1": {"headline": "CEO @ BatchCo", "about": "Tech stuff"},
            "https://linkedin.com/in/batch2": {"headline": "CTO @ BatchCo2", "about": "Finance stuff"},
        }

        mock_deepseek_response = MagicMock()
        mock_deepseek_response.status_code = 200
        mock_deepseek_response.raise_for_status = MagicMock()
        mock_deepseek_response.json.return_value = {
            "choices": [{"message": {"content": "Hey Batch\n\nPersonalized message here"}}]
        }

        mock_heyreach = MagicMock()
        mock_heyreach.add_leads_to_list = AsyncMock(return_value={"addedCount": 2})

        with (
            patch("app.database.async_session_factory", session_factory),
            patch("app.services.buying_signal_outreach.scrape_profiles_batch", return_value=mock_profiles),
            patch("httpx.AsyncClient") as mock_client_cls,
            patch("app.services.heyreach.get_heyreach_client", return_value=mock_heyreach),
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_deepseek_response)
            mock_client_cls.return_value = mock_client

            from app.services.buying_signal_outreach import process_buying_signal_batch
            result = await process_buying_signal_batch()

        assert result["processed"] == 2
        assert result["messages_generated"] == 2
        assert result["uploaded"] == 2
        assert result["errors"] == 0

        # Verify DB was updated
        async with session_factory() as session:
            prospects = (await session.execute(
                select(Prospect).where(Prospect.source_type == ProspectSource.BUYING_SIGNAL)
            )).scalars().all()
            for p in prospects:
                assert p.personalized_message is not None
                assert p.heyreach_list_id == 480247
                assert p.heyreach_uploaded_at is not None

    @pytest.mark.asyncio
    async def test_no_prospects_returns_zeros(self, test_db_engine):
        """Should return zeros when no unprocessed prospects exist."""
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False,
        )

        with patch("app.database.async_session_factory", session_factory):
            from app.services.buying_signal_outreach import process_buying_signal_batch
            result = await process_buying_signal_batch()

        assert result["processed"] == 0
        assert result["messages_generated"] == 0


class TestManualTriggerEndpoint:
    """Tests for POST /buying-signal/process endpoint."""

    @pytest.mark.asyncio
    async def test_endpoint_returns_processing(self, test_db_engine):
        """Should return 200 with processing status."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False,
        )

        import app.main as main_module
        original_factory = main_module.async_session_factory
        main_module.async_session_factory = session_factory

        try:
            from httpx import ASGITransport, AsyncClient
            transport = ASGITransport(app=main_module.app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/buying-signal/process")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "processing"
        finally:
            main_module.async_session_factory = original_factory


class TestSchedulerRegistration:
    """Tests for scheduler job registration."""

    @pytest.mark.asyncio
    async def test_buying_signal_job_registered(self):
        """Buying signal outreach job should be registered in scheduler."""
        from app.services.scheduler import SchedulerService
        scheduler = SchedulerService()
        scheduler.start()

        job = scheduler._scheduler.get_job('buying_signal_outreach')
        assert job is not None
        assert job.name == 'Buying signal outreach batch'

        scheduler.shutdown(wait=False)
