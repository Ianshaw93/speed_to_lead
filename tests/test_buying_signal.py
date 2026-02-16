"""Tests for the /buying-signal endpoint DB persistence."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Prospect, ProspectSource


def _make_payload(**overrides) -> dict:
    """Build a realistic Gojiberry buying signal payload."""
    base = {
        "id": 2128931,
        "firstName": "Juan",
        "lastName": "Romero",
        "fullName": "Juan Romero",
        "profileBaseline": "Founder @ Axolop | Helping B2B founders automate",
        "location": "Greater Tampa Bay Area, United States",
        "jobTitle": "Founder / CEO",
        "company": "Axolop",
        "companySize": "2 - 10",
        "industry": "Software Development",
        "email": None,
        "phone": None,
        "profileId": "ACoAAEHJtiwBxw8iJMgo0lTM",
        "profileUrl": "https://www.linkedin.com/in/ACoAAEHJtiwBxw8iJMgo0lTM",
        "linkedinIdentifier": "juansbiz",
        "intent": "Just engaged with an industry expert",
        "intent_type": "INFLUENCER_PAGE_URL",
        "intent_keyword": "https://www.linkedin.com/in/naim-ahmed-753768174/",
        "scoring": "0.97",
        "score_reasoning": "Role matches ICP; industry aligns with core tech field.",
        "total_scoring": "2.00",
    }
    base.update(overrides)
    return base


class TestBuyingSignalPersistence:
    """Tests for persisting buying signal prospects to DB."""

    @pytest.mark.asyncio
    async def test_creates_prospect(self, test_db_engine):
        """New buying signal should create a Prospect record."""
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
                response = await client.post("/buying-signal", json=_make_payload())

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "created"
            assert "juansbiz" in data["linkedin_url"]

            # Verify DB record
            async with session_factory() as session:
                result = await session.execute(
                    select(Prospect).where(
                        Prospect.linkedin_url == "https://linkedin.com/in/juansbiz"
                    )
                )
                prospect = result.scalar_one()
                assert prospect.full_name == "Juan Romero"
                assert prospect.first_name == "Juan"
                assert prospect.last_name == "Romero"
                assert prospect.job_title == "Founder / CEO"
                assert prospect.company_name == "Axolop"
                assert prospect.company_industry == "Software Development"
                assert prospect.location == "Greater Tampa Bay Area, United States"
                assert prospect.headline == "Founder @ Axolop | Helping B2B founders automate"
                assert prospect.source_type == ProspectSource.BUYING_SIGNAL
                assert prospect.source_keyword == "https://www.linkedin.com/in/naim-ahmed-753768174/"
                assert prospect.icp_match is True
                assert prospect.icp_reason == "Role matches ICP; industry aligns with core tech field."
        finally:
            main_module.async_session_factory = original_factory

    @pytest.mark.asyncio
    async def test_skips_duplicate(self, test_db_engine):
        """Duplicate linkedinIdentifier should return 'duplicate', not insert."""
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False,
        )

        # Seed existing prospect
        async with session_factory() as session:
            session.add(Prospect(
                linkedin_url="https://linkedin.com/in/juansbiz",
                full_name="Juan Romero",
                source_type=ProspectSource.BUYING_SIGNAL,
            ))
            await session.commit()

        import app.main as main_module
        original_factory = main_module.async_session_factory
        main_module.async_session_factory = session_factory

        try:
            from httpx import ASGITransport, AsyncClient
            transport = ASGITransport(app=main_module.app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/buying-signal", json=_make_payload())

            assert response.status_code == 200
            assert response.json()["status"] == "duplicate"

            # Verify only one record exists
            async with session_factory() as session:
                result = await session.execute(select(Prospect))
                prospects = result.scalars().all()
                assert len(prospects) == 1
        finally:
            main_module.async_session_factory = original_factory

    @pytest.mark.asyncio
    async def test_missing_linkedin_identifier(self, test_db_engine):
        """Payload without linkedinIdentifier should not persist."""
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
                payload = _make_payload()
                del payload["linkedinIdentifier"]
                response = await client.post("/buying-signal", json=payload)

            assert response.status_code == 200
            assert response.json()["persisted"] is False
        finally:
            main_module.async_session_factory = original_factory

    @pytest.mark.asyncio
    async def test_email_persisted_when_present(self, test_db_engine):
        """If email is present in payload, it should be saved."""
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
                response = await client.post(
                    "/buying-signal",
                    json=_make_payload(
                        linkedinIdentifier="withmail",
                        email="juan@axolop.com",
                    ),
                )

            assert response.status_code == 200
            assert response.json()["status"] == "created"

            async with session_factory() as session:
                result = await session.execute(
                    select(Prospect).where(
                        Prospect.linkedin_url == "https://linkedin.com/in/withmail"
                    )
                )
                prospect = result.scalar_one()
                assert prospect.email == "juan@axolop.com"
        finally:
            main_module.async_session_factory = original_factory
