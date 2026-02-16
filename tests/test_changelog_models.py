"""Tests for Changelog and PromptVersion models."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Changelog,
    ChangelogCategory,
    PromptVersion,
    Prospect,
    ProspectSource,
)


class TestChangelogCategoryEnum:
    """Tests for the ChangelogCategory enum."""

    def test_all_values_present(self):
        """ChangelogCategory should have all expected values."""
        expected = {
            "prompt",
            "icp_filter",
            "prospect_source",
            "pipeline_config",
            "validation",
            "model",
            "ab_test",
            "infrastructure",
            "heyreach",
            "stage_prompt",
        }
        actual = {c.value for c in ChangelogCategory}
        assert actual == expected

    def test_is_string_enum(self):
        """ChangelogCategory values should be strings."""
        for c in ChangelogCategory:
            assert isinstance(c.value, str)


class TestPromptVersionModel:
    """Tests for the PromptVersion model."""

    @pytest.mark.asyncio
    async def test_create_prompt_version(self, test_db_session: AsyncSession):
        """Should create a prompt version with required fields."""
        pv = PromptVersion(
            prompt_name="LINKEDIN_5_LINE_DM_PROMPT",
            prompt_hash="a" * 64,
            content="Hello {name}, I noticed your work at {company}...",
        )
        test_db_session.add(pv)
        await test_db_session.commit()
        await test_db_session.refresh(pv)

        assert pv.id is not None
        assert isinstance(pv.id, uuid.UUID)
        assert pv.prompt_name == "LINKEDIN_5_LINE_DM_PROMPT"
        assert pv.prompt_hash == "a" * 64
        assert "Hello {name}" in pv.content
        assert pv.git_commit is None
        assert pv.created_at is not None

    @pytest.mark.asyncio
    async def test_prompt_version_with_git_commit(self, test_db_session: AsyncSession):
        """Should store git commit hash."""
        pv = PromptVersion(
            prompt_name="TEST_PROMPT",
            prompt_hash="b" * 64,
            content="Test content",
            git_commit="abc1234567890abcdef1234567890abcdef12345",
        )
        test_db_session.add(pv)
        await test_db_session.commit()
        await test_db_session.refresh(pv)

        assert pv.git_commit == "abc1234567890abcdef1234567890abcdef12345"

    @pytest.mark.asyncio
    async def test_prompt_hash_unique(self, test_db_session: AsyncSession):
        """prompt_hash should be unique across rows."""
        pv1 = PromptVersion(
            prompt_name="PROMPT_A",
            prompt_hash="c" * 64,
            content="Content A",
        )
        test_db_session.add(pv1)
        await test_db_session.commit()

        pv2 = PromptVersion(
            prompt_name="PROMPT_B",
            prompt_hash="c" * 64,  # same hash
            content="Content B",
        )
        test_db_session.add(pv2)

        with pytest.raises(Exception):  # IntegrityError
            await test_db_session.commit()

    def test_prompt_version_has_required_columns(self):
        """PromptVersion should have all required columns."""
        mapper = inspect(PromptVersion)
        column_names = {col.key for col in mapper.columns}

        required = {
            "id",
            "prompt_name",
            "prompt_hash",
            "content",
            "git_commit",
            "created_at",
        }
        missing = required - column_names
        assert not missing, f"Missing columns in PromptVersion: {missing}"


class TestChangelogModel:
    """Tests for the Changelog model."""

    @pytest.mark.asyncio
    async def test_create_changelog_entry(self, test_db_session: AsyncSession):
        """Should create a changelog entry with all fields."""
        entry = Changelog(
            timestamp=datetime(2026, 1, 22, tzinfo=timezone.utc),
            category=ChangelogCategory.PROMPT,
            component="LINKEDIN_5_LINE_DM_PROMPT",
            change_type="added",
            description="Initial prompt template created",
            git_commit="64d5f97",
        )
        test_db_session.add(entry)
        await test_db_session.commit()
        await test_db_session.refresh(entry)

        assert entry.id is not None
        assert isinstance(entry.id, uuid.UUID)
        assert entry.category == ChangelogCategory.PROMPT
        assert entry.component == "LINKEDIN_5_LINE_DM_PROMPT"
        assert entry.change_type == "added"
        assert entry.description == "Initial prompt template created"
        assert entry.git_commit == "64d5f97"
        assert entry.details is None
        assert entry.created_at is not None

    @pytest.mark.asyncio
    async def test_changelog_with_details_json(self, test_db_session: AsyncSession):
        """Should store structured JSON details."""
        entry = Changelog(
            timestamp=datetime(2026, 2, 4, tzinfo=timezone.utc),
            category=ChangelogCategory.PIPELINE_CONFIG,
            component="profile_caching",
            change_type="added",
            description="Profile cache to avoid re-scraping",
            details={"cache_path": ".tmp/profile_cache.json", "savings_per_hit": 0.025},
        )
        test_db_session.add(entry)
        await test_db_session.commit()
        await test_db_session.refresh(entry)

        assert entry.details is not None
        assert entry.details["cache_path"] == ".tmp/profile_cache.json"
        assert entry.details["savings_per_hit"] == 0.025

    @pytest.mark.asyncio
    async def test_changelog_all_categories(self, test_db_session: AsyncSession):
        """Should support all ChangelogCategory values."""
        for i, cat in enumerate(ChangelogCategory):
            entry = Changelog(
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                category=cat,
                component=f"component_{i}",
                change_type="added",
                description=f"Test for {cat.value}",
            )
            test_db_session.add(entry)

        await test_db_session.commit()

        result = await test_db_session.execute(select(Changelog))
        entries = result.scalars().all()
        categories = {e.category for e in entries}
        assert categories == set(ChangelogCategory)

    @pytest.mark.asyncio
    async def test_changelog_query_by_category(self, test_db_session: AsyncSession):
        """Should be queryable by category."""
        for cat in [ChangelogCategory.PROMPT, ChangelogCategory.PROMPT, ChangelogCategory.MODEL]:
            entry = Changelog(
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                category=cat,
                component="test",
                change_type="added",
                description="Test",
            )
            test_db_session.add(entry)

        await test_db_session.commit()

        result = await test_db_session.execute(
            select(Changelog).where(Changelog.category == ChangelogCategory.PROMPT)
        )
        prompts = result.scalars().all()
        assert len(prompts) == 2

    def test_changelog_has_required_columns(self):
        """Changelog should have all required columns."""
        mapper = inspect(Changelog)
        column_names = {col.key for col in mapper.columns}

        required = {
            "id",
            "timestamp",
            "category",
            "component",
            "change_type",
            "description",
            "details",
            "git_commit",
            "created_at",
        }
        missing = required - column_names
        assert not missing, f"Missing columns in Changelog: {missing}"


class TestProspectPromptVersionFK:
    """Tests for the prompt_version_id FK on Prospect."""

    @pytest.mark.asyncio
    async def test_prospect_has_prompt_version_id(self, test_db_session: AsyncSession):
        """Prospect should have a nullable prompt_version_id field."""
        prospect = Prospect(
            linkedin_url="https://linkedin.com/in/test-changelog",
            full_name="Test User",
            source_type=ProspectSource.OTHER,
        )
        test_db_session.add(prospect)
        await test_db_session.commit()
        await test_db_session.refresh(prospect)

        assert prospect.prompt_version_id is None

    @pytest.mark.asyncio
    async def test_prospect_linked_to_prompt_version(self, test_db_session: AsyncSession):
        """Prospect should link to a PromptVersion via FK."""
        pv = PromptVersion(
            prompt_name="DM_PROMPT",
            prompt_hash="d" * 64,
            content="Template content",
        )
        test_db_session.add(pv)
        await test_db_session.commit()
        await test_db_session.refresh(pv)

        prospect = Prospect(
            linkedin_url="https://linkedin.com/in/linked-prompt",
            full_name="Linked User",
            source_type=ProspectSource.COLD_OUTREACH,
            prompt_version_id=pv.id,
        )
        test_db_session.add(prospect)
        await test_db_session.commit()
        await test_db_session.refresh(prospect)

        assert prospect.prompt_version_id == pv.id

    def test_prospect_column_exists(self):
        """Prospect model should have prompt_version_id column."""
        mapper = inspect(Prospect)
        column_names = {col.key for col in mapper.columns}
        assert "prompt_version_id" in column_names
