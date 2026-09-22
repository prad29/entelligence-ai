"""
DB-backed cache of theater-name -> resolved showtimes-page URL
(`app.models.TheaterSiteUrl`), for site_adapters that resolve a URL via a
search step (currently just AMC, via SerpApi organic results). Real
replacement for the research session's throwaway SQLite AMC-URL cache.

Fails open (cache miss / no-op write) if the table isn't there yet, same
convention as serp_key_rotation.py — this is a pure optimization, never a
correctness dependency: a cache miss just means the adapter re-resolves the
URL via SerpApi on the next call.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)

_TABLE_MISSING_ERRORS = (OperationalError, ProgrammingError)


def get_cached_url(theater_name: str) -> Optional[str]:
    from sqlmodel import Session

    from app.database import engine
    from app.models import TheaterSiteUrl

    try:
        with Session(engine) as session:
            row = session.get(TheaterSiteUrl, theater_name)
            return row.resolved_url if row else None
    except _TABLE_MISSING_ERRORS as exc:
        logger.warning("site_url_cache: table unavailable on read, treating as cache miss: %s", exc)
        return None


def save_resolved_url(theater_name: str, circuit_name: str, url: str, source: str = "serpapi") -> None:
    from sqlalchemy.dialects import postgresql, sqlite
    from sqlmodel import Session

    from app.database import engine
    from app.models import TheaterSiteUrl

    try:
        with Session(engine) as session:
            dialect_mod = postgresql if session.bind is not None and session.bind.dialect.name == "postgresql" else sqlite
            stmt = dialect_mod.insert(TheaterSiteUrl).values(
                theater_name=theater_name,
                circuit_name=circuit_name,
                resolved_url=url,
                resolved_at=datetime.utcnow(),
                source=source,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["theater_name"],
                set_={
                    "circuit_name": stmt.excluded.circuit_name,
                    "resolved_url": stmt.excluded.resolved_url,
                    "resolved_at": stmt.excluded.resolved_at,
                    "source": stmt.excluded.source,
                },
            )
            session.execute(stmt)
            session.commit()
    except _TABLE_MISSING_ERRORS as exc:
        logger.warning("site_url_cache: table unavailable on write, dropping cache write: %s", exc)
