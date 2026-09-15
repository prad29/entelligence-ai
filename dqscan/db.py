"""Single choke point for all SQL execution against movies_shows.

Every statement passes through assert_read_only_sql before it touches the
DB — no bypass flag, by design, since this module is the safety boundary
that keeps dqscan read-only.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL

from dqscan.config import DatabaseConfig

logger = logging.getLogger("dqscan.db")

_ALLOWED_PREFIXES = ("SELECT", "WITH")

# Applies only to plain SELECTs (every rule class in this pack compiles to
# one) — a rule that scans the whole table via an unindexed GROUP BY or
# window function must not be able to block the entire run.
_LEADING_SELECT = re.compile(r"^(\s*)(SELECT)\b", re.IGNORECASE)


def get_engine(db_config: DatabaseConfig, *, read_timeout_seconds: int | None = None) -> Engine:
    # URL.create escapes user/password -- a raw f-string interpolation breaks
    # the moment a password contains an unescaped URL-special character (e.g.
    # "@", ":", "/"), since the parser can no longer tell where the
    # credentials end and the host begins.
    url = URL.create(
        "mysql+pymysql",
        username=db_config.user,
        password=db_config.password,
        host=db_config.host,
        port=db_config.port,
        database=db_config.schema,
    )
    connect_args = {}
    if read_timeout_seconds:
        # Socket-level backstop for statements the MAX_EXECUTION_TIME hint
        # can't reach (anything not starting with a bare SELECT). Killing
        # the connection is fine — pool_pre_ping keeps a dead one from being
        # handed back out.
        connect_args["read_timeout"] = read_timeout_seconds
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    # AUTOCOMMIT + no engine.begin(): there is never an open transaction to
    # accidentally write inside, which is most of the read-only guarantee.
    return engine.execution_options(isolation_level="AUTOCOMMIT")


def assert_read_only_sql(sql: str) -> None:
    stripped = sql.strip()
    if not stripped.upper().startswith(_ALLOWED_PREFIXES):
        raise RuntimeError(
            f"Refusing to execute non-read-only statement: {stripped[:80]!r}"
        )


def _with_timeout_hint(sql: str, timeout_ms: int | None) -> str:
    if not timeout_ms:
        return sql
    if not _LEADING_SELECT.match(sql):
        # No current rule compiles to a WITH-prefixed statement, so this is
        # a forward-looking gap, not a live bug — the read_timeout socket
        # backstop (see get_engine) still bounds it, just less precisely.
        logger.warning("No MAX_EXECUTION_TIME hint applied (statement is not a bare SELECT): %s", sql[:80])
        return sql
    return _LEADING_SELECT.sub(rf"\1\2 /*+ MAX_EXECUTION_TIME({timeout_ms}) */", sql, count=1)


def run_query(engine: Engine, sql: str, params: dict | None = None, timeout_ms: int | None = None) -> list[dict]:
    assert_read_only_sql(sql)
    params = params or {}
    hinted_sql = _with_timeout_hint(sql, timeout_ms)
    logger.debug("Executing SQL: %s | params=%s", hinted_sql, params)
    with engine.connect() as conn:
        result = conn.execute(text(hinted_sql), params)
        rows = [dict(row._mapping) for row in result]
    return rows


def run_scalar(engine: Engine, sql: str, params: dict | None = None, timeout_ms: int | None = None) -> Any:
    rows = run_query(engine, sql, params, timeout_ms)
    if not rows:
        return None
    return next(iter(rows[0].values()))
