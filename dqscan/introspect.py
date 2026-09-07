"""Schema introspection for movie_shows, used to guard rules against
columns that don't exist in a given environment."""

from __future__ import annotations

import logging

from sqlalchemy.engine import Engine

from dqscan import db

logger = logging.getLogger("dqscan.introspect")


def get_existing_columns(
    engine: Engine, schema: str, table: str = "movies_shows", *, timeout_ms: int | None = None
) -> set[str]:
    sql = """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table
    """
    rows = db.run_query(engine, sql, {"schema": schema, "table": table}, timeout_ms)
    columns = {row["COLUMN_NAME"] for row in rows}
    logger.debug("Existing columns for %s.%s: %s", schema, table, sorted(columns))
    return columns
