"""Obsidian vault reader — local file with MinIO S3 fallback.

Reads Obsidian notes for personalising outreach. Locally reads the vault
directory directly; on Railway falls back to MinIO where Obsidian syncs
via the "Remotely Save" plugin.
"""

import logging
import time
from functools import lru_cache
from pathlib import Path

from minio import Minio

from app.config import settings

logger = logging.getLogger(__name__)

# Known local vault path (Windows)
_DEFAULT_VAULT_PATH = (
    r"C:\Users\IanShaw\OneDrive - Fire Dynamics Group Limited"
    r"\Documents\Obsidian Vault"
)

# Default cache TTL: 1 hour
_DEFAULT_CACHE_TTL = 3600


class ObsidianReader:
    """Reads Obsidian notes from local vault or MinIO S3."""

    def __init__(
        self,
        local_vault_path: str = "",
        minio_client: Minio | None = None,
        minio_bucket: str = "obsidian-vault",
        cache_ttl: int = _DEFAULT_CACHE_TTL,
    ):
        self._local_vault_path = local_vault_path
        self._minio_client = minio_client
        self._minio_bucket = minio_bucket
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[str, float]] = {}  # path -> (content, timestamp)

    def read_note(self, path: str) -> str | None:
        """Read a note by vault-relative path. Returns content or None."""
        # Check cache
        if path in self._cache:
            content, ts = self._cache[path]
            if time.time() - ts < self._cache_ttl:
                return content

        # Try local first
        content = self._read_local(path)
        if content is not None:
            self._cache[path] = (content, time.time())
            return content

        # Fall back to MinIO
        content = self._read_minio(path)
        if content is not None:
            self._cache[path] = (content, time.time())
            return content

        logger.warning("Could not read Obsidian note: %s", path)
        return None

    def _read_local(self, path: str) -> str | None:
        """Try reading from local vault."""
        # Explicit path first
        if self._local_vault_path:
            vault = Path(self._local_vault_path)
        else:
            # Auto-detect known Windows path
            vault = Path(_DEFAULT_VAULT_PATH)

        if not vault.exists():
            return None

        note = vault / path
        if not note.exists():
            return None

        try:
            return note.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to read local note %s: %s", path, e)
            return None

    def _read_minio(self, path: str) -> str | None:
        """Try reading from MinIO S3."""
        if self._minio_client is None:
            return None

        try:
            response = self._minio_client.get_object(self._minio_bucket, path)
            content = response.read().decode("utf-8")
            response.close()
            response.release_conn()
            return content
        except Exception as e:
            logger.warning("Failed to read note from MinIO %s: %s", path, e)
            return None


@lru_cache
def get_obsidian_reader() -> ObsidianReader:
    """Get cached ObsidianReader singleton."""
    minio_client = None
    if settings.minio_endpoint and settings.minio_access_key:
        minio_client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=True,
        )
        logger.info("MinIO client configured for Obsidian vault")

    return ObsidianReader(
        local_vault_path=settings.obsidian_vault_path,
        minio_client=minio_client,
        minio_bucket=settings.minio_bucket,
    )


def get_common_ground() -> str | None:
    """Read Ian's common-ground notes for prospect personalisation."""
    return get_obsidian_reader().read_note(
        "Ian Personal/Common Ground with Prospects.md"
    )
