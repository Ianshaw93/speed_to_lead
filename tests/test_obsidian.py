"""Tests for the Obsidian reader utility."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.obsidian import ObsidianReader, get_common_ground, get_obsidian_reader


SAMPLE_NOTE = "# Common Ground\n\n- Likes hiking\n- Based in UK"
VAULT_PATH = r"C:\Users\IanShaw\OneDrive - Fire Dynamics Group Limited\Documents\Obsidian Vault"


class TestObsidianReaderLocal:
    """Test reading notes from local vault."""

    def test_reads_local_file_when_exists(self, tmp_path):
        """Local vault path is preferred when the file exists."""
        note_dir = tmp_path / "Ian Personal"
        note_dir.mkdir()
        note_file = note_dir / "Common Ground with Prospects.md"
        note_file.write_text(SAMPLE_NOTE, encoding="utf-8")

        reader = ObsidianReader(local_vault_path=str(tmp_path))
        result = reader.read_note("Ian Personal/Common Ground with Prospects.md")

        assert result == SAMPLE_NOTE

    def test_returns_none_when_local_file_missing_and_no_minio(self, tmp_path):
        """Returns None when local file doesn't exist and MinIO isn't configured."""
        reader = ObsidianReader(local_vault_path=str(tmp_path))
        result = reader.read_note("nonexistent/note.md")

        assert result is None

    def test_auto_detects_known_windows_path(self):
        """Falls back to known Windows vault path when no explicit path given."""
        with patch("app.services.obsidian.Path") as mock_path_cls:
            mock_vault = MagicMock()
            mock_vault.exists.return_value = True
            mock_note = MagicMock()
            mock_note.exists.return_value = True
            mock_note.read_text.return_value = SAMPLE_NOTE
            mock_vault.__truediv__ = MagicMock(return_value=mock_note)
            mock_path_cls.return_value = mock_vault

            reader = ObsidianReader(local_vault_path="")
            result = reader.read_note("some/note.md")

            assert result == SAMPLE_NOTE


class TestObsidianReaderMinIO:
    """Test reading notes from MinIO S3."""

    def test_falls_back_to_minio_when_local_missing(self):
        """Uses MinIO when local vault is not available."""
        mock_client = MagicMock()
        response = MagicMock()
        response.read.return_value = SAMPLE_NOTE.encode("utf-8")
        response.close.return_value = None
        response.release_conn.return_value = None
        mock_client.get_object.return_value = response

        reader = ObsidianReader(
            local_vault_path="/nonexistent/path",
            minio_client=mock_client,
            minio_bucket="obsidian-vault",
        )
        result = reader.read_note("Ian Personal/Common Ground with Prospects.md")

        assert result == SAMPLE_NOTE
        mock_client.get_object.assert_called_once_with(
            "obsidian-vault",
            "Ian Personal/Common Ground with Prospects.md",
        )

    def test_returns_none_when_minio_fails(self):
        """Returns None gracefully when MinIO raises an error."""
        mock_client = MagicMock()
        mock_client.get_object.side_effect = Exception("Connection refused")

        reader = ObsidianReader(
            local_vault_path="/nonexistent/path",
            minio_client=mock_client,
            minio_bucket="obsidian-vault",
        )
        result = reader.read_note("some/note.md")

        assert result is None


class TestObsidianReaderCaching:
    """Test TTL-based caching."""

    def test_second_read_uses_cache(self, tmp_path):
        """Second call within TTL returns cached content without re-reading."""
        note_dir = tmp_path / "notes"
        note_dir.mkdir()
        note_file = note_dir / "test.md"
        note_file.write_text(SAMPLE_NOTE, encoding="utf-8")

        reader = ObsidianReader(local_vault_path=str(tmp_path), cache_ttl=3600)

        result1 = reader.read_note("notes/test.md")
        # Overwrite the file — cached value should still be returned
        note_file.write_text("CHANGED CONTENT", encoding="utf-8")
        result2 = reader.read_note("notes/test.md")

        assert result1 == SAMPLE_NOTE
        assert result2 == SAMPLE_NOTE

    def test_cache_expires_after_ttl(self, tmp_path):
        """After TTL expires, the note is re-read from source."""
        note_dir = tmp_path / "notes"
        note_dir.mkdir()
        note_file = note_dir / "test.md"
        note_file.write_text(SAMPLE_NOTE, encoding="utf-8")

        reader = ObsidianReader(local_vault_path=str(tmp_path), cache_ttl=0)

        result1 = reader.read_note("notes/test.md")
        note_file.write_text("UPDATED", encoding="utf-8")
        # TTL=0 means always expired
        time.sleep(0.01)
        result2 = reader.read_note("notes/test.md")

        assert result1 == SAMPLE_NOTE
        assert result2 == "UPDATED"


class TestConvenienceFunctions:
    """Test module-level convenience functions."""

    def test_get_common_ground_returns_note_content(self):
        """get_common_ground() reads the correct vault-relative path."""
        with patch("app.services.obsidian.get_obsidian_reader") as mock_get:
            mock_reader = MagicMock()
            mock_reader.read_note.return_value = SAMPLE_NOTE
            mock_get.return_value = mock_reader

            result = get_common_ground()

            assert result == SAMPLE_NOTE
            mock_reader.read_note.assert_called_once_with(
                "Ian Personal/Common Ground with Prospects.md"
            )

    def test_get_common_ground_returns_none_on_failure(self):
        """get_common_ground() returns None when the note can't be read."""
        with patch("app.services.obsidian.get_obsidian_reader") as mock_get:
            mock_reader = MagicMock()
            mock_reader.read_note.return_value = None
            mock_get.return_value = mock_reader

            result = get_common_ground()

            assert result is None


class TestGetObsidianReader:
    """Test the singleton factory."""

    def test_returns_obsidian_reader_instance(self):
        """get_obsidian_reader() returns an ObsidianReader."""
        with patch("app.services.obsidian.settings") as mock_settings:
            mock_settings.obsidian_vault_path = ""
            mock_settings.minio_endpoint = ""
            mock_settings.minio_access_key = ""
            mock_settings.minio_secret_key = ""
            mock_settings.minio_bucket = "obsidian-vault"

            # Clear lru_cache
            get_obsidian_reader.cache_clear()
            reader = get_obsidian_reader()

            assert isinstance(reader, ObsidianReader)
            get_obsidian_reader.cache_clear()

    def test_creates_minio_client_when_configured(self):
        """Creates a MinIO client when credentials are provided."""
        with patch("app.services.obsidian.settings") as mock_settings, \
             patch("app.services.obsidian.Minio") as mock_minio_cls:
            mock_settings.obsidian_vault_path = ""
            mock_settings.minio_endpoint = "bucket.example.com"
            mock_settings.minio_access_key = "access-key"
            mock_settings.minio_secret_key = "secret-key"
            mock_settings.minio_bucket = "obsidian-vault"

            get_obsidian_reader.cache_clear()
            reader = get_obsidian_reader()

            mock_minio_cls.assert_called_once_with(
                "bucket.example.com",
                access_key="access-key",
                secret_key="secret-key",
                secure=True,
            )
            assert reader._minio_client is not None
            get_obsidian_reader.cache_clear()
