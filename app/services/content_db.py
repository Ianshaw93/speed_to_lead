"""Sync SQLAlchemy session for contentCreator's Postgres database.

Used by trend scout to write TrendingTopic rows into the content DB.
Kept lightweight — only defines the single model we need to write to.
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy import Column, Integer, JSON, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

ContentBase = declarative_base()

# ---------------------------------------------------------------------------
# Model (mirrors contentCreator/execution/database.py TrendingTopic)
# ---------------------------------------------------------------------------


class TrendingTopic(ContentBase):
    __tablename__ = "trending_topics"

    id = Column(String, primary_key=True)
    topic = Column(Text, nullable=False)
    summary = Column(Text)
    source_urls = Column(JSON, default=list)
    relevance_score = Column(Integer)
    content_angles = Column(JSON, default=list)
    search_query = Column(String)
    batch_id = Column(String)
    status = Column(String, default="new")
    source_platform = Column(String)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    notes = Column(Text)


# ---------------------------------------------------------------------------
# Engine / session helpers (lazy-initialised)
# ---------------------------------------------------------------------------

_engine = None
_SessionFactory = None


def _get_content_engine():
    global _engine
    if _engine is None:
        url = settings.content_db_url
        if not url:
            raise RuntimeError("CONTENT_DB_URL is not configured")
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def _get_content_session() -> Session:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=_get_content_engine(),
            autocommit=False,
            autoflush=False,
        )
    return _SessionFactory()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def save_trending_topic(
    topic: str,
    summary: str | None = None,
    source_urls: list | None = None,
    relevance_score: int | None = None,
    content_angles: list | None = None,
    search_query: str | None = None,
    batch_id: str | None = None,
    source_platform: str | None = None,
    notes: str | None = None,
) -> dict:
    """Save a trending topic to the contentCreator DB.

    Returns:
        Dict representation of the saved row.
    """
    now = datetime.now().isoformat()
    entry_id = str(uuid.uuid4())[:8]
    src_urls = source_urls or []
    angles = content_angles or []

    entry = TrendingTopic(
        id=entry_id,
        topic=topic,
        summary=summary,
        source_urls=src_urls,
        relevance_score=relevance_score,
        content_angles=angles,
        search_query=search_query,
        batch_id=batch_id,
        status="new",
        source_platform=source_platform,
        created_at=now,
        updated_at=now,
        notes=notes,
    )

    session = _get_content_session()
    try:
        session.add(entry)
        session.commit()
        logger.info(f"Saved trending topic '{topic}' (batch={batch_id})")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    # Build return dict from known values (not the detached ORM object)
    return {
        "id": entry_id,
        "topic": topic,
        "summary": summary,
        "source_urls": src_urls,
        "relevance_score": relevance_score,
        "content_angles": angles,
        "search_query": search_query,
        "batch_id": batch_id,
        "status": "new",
        "source_platform": source_platform,
        "created_at": now,
        "updated_at": now,
    }
