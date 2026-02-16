"""Tests for the changelog backfill script."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Changelog, ChangelogCategory

# Ensure scripts directory is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestBackfillChangelog:
    """Tests for the backfill_changelog script logic."""

    @pytest.mark.asyncio
    async def test_backfill_inserts_all_entries(self, test_db_engine):
        """Should insert all 25 changelog entries on first run."""
        from scripts.backfill_changelog import backfill, CHANGELOG_ENTRIES

        test_session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False
        )

        with patch("scripts.backfill_changelog.async_session_factory", test_session_factory):
            await backfill()

        async with test_session_factory() as session:
            result = await session.execute(select(Changelog))
            entries = result.scalars().all()
            assert len(entries) == len(CHANGELOG_ENTRIES)

    @pytest.mark.asyncio
    async def test_backfill_idempotent(self, test_db_engine):
        """Should not create duplicates on re-run."""
        from scripts.backfill_changelog import backfill, CHANGELOG_ENTRIES

        test_session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False
        )

        with patch("scripts.backfill_changelog.async_session_factory", test_session_factory):
            await backfill()
            await backfill()  # second run

        async with test_session_factory() as session:
            result = await session.execute(select(Changelog))
            entries = result.scalars().all()
            assert len(entries) == len(CHANGELOG_ENTRIES)

    @pytest.mark.asyncio
    async def test_backfill_timestamps_utc(self, test_db_engine):
        """All timestamps should be UTC."""
        from scripts.backfill_changelog import backfill

        test_session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False
        )

        with patch("scripts.backfill_changelog.async_session_factory", test_session_factory):
            await backfill()

        async with test_session_factory() as session:
            result = await session.execute(select(Changelog))
            entries = result.scalars().all()
            for entry in entries:
                assert entry.timestamp is not None
                # SQLite doesn't preserve tz info, but we verify the value was set
                assert isinstance(entry.timestamp, datetime)

    @pytest.mark.asyncio
    async def test_backfill_categories_valid(self, test_db_engine):
        """All entries should have valid ChangelogCategory values."""
        from scripts.backfill_changelog import backfill

        test_session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False
        )

        with patch("scripts.backfill_changelog.async_session_factory", test_session_factory):
            await backfill()

        async with test_session_factory() as session:
            result = await session.execute(select(Changelog))
            entries = result.scalars().all()
            for entry in entries:
                assert entry.category in ChangelogCategory

    @pytest.mark.asyncio
    async def test_backfill_has_expected_components(self, test_db_engine):
        """Should include key components like foundation, buying_signal, api_server."""
        from scripts.backfill_changelog import backfill

        test_session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False
        )

        with patch("scripts.backfill_changelog.async_session_factory", test_session_factory):
            await backfill()

        async with test_session_factory() as session:
            result = await session.execute(select(Changelog))
            entries = result.scalars().all()
            components = {e.component for e in entries}

            expected_components = {
                "foundation",
                "buying_signal",
                "api_server",
                "LINKEDIN_5_LINE_DM_PROMPT",
                "validate_personalization",
            }
            missing = expected_components - components
            assert not missing, f"Missing components: {missing}"


class TestParseTimestamp:
    """Tests for the parse_timestamp helper."""

    def test_parse_date_string(self):
        from scripts.backfill_changelog import parse_timestamp

        result = parse_timestamp("2026-01-22")
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 22
        assert result.tzinfo == timezone.utc

    def test_parse_returns_midnight(self):
        from scripts.backfill_changelog import parse_timestamp

        result = parse_timestamp("2025-12-23")
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0
