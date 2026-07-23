"""
Read-only client for the production MySQL DB (source of truth for Movie
Master data), used by the "Sync from Production DB" feature.

fq_movie_master / fq_movie_master_intl live in a separate MySQL database from
this app's own Postgres DB — psycopg2 (this app's only DB driver until now)
cannot connect to it, hence pymysql here.

Plain functions (not a class) so tests can monkeypatch the connection factory
directly, matching the existing _client()/_get_redis() pattern used elsewhere
in this codebase (batch_storage.py, agentic_match_task.py).
"""

from __future__ import annotations

from contextlib import closing
from typing import Iterator

from app.config import settings

_FETCH_BATCH_SIZE = 5000

_DOMESTIC_COLUMNS = (
    "id", "movie_title", "release_date", "cover_image", "director",
    "`cast`", "running_time", "parent_id", "search_tags", "title_tag", "short_name",
)

_INTL_COLUMNS = (
    "id", "movie_id", "movie_title", "master_movie_title", "release_date",
    "studio", "country_id", "country", "rating", "genre", "genre2",
    "running_time", "updated_on",
)


def _require_prod_db_host() -> None:
    if not settings.PROD_DB_HOST:
        raise RuntimeError(
            "PROD_DB_HOST is not configured — syncing Movie Master from the "
            "production DB requires PROD_DB_HOST/PORT/DATABASE/USERNAME/PASSWORD."
        )


def _prod_db_connection():
    import pymysql

    _require_prod_db_host()
    return pymysql.connect(
        host=settings.PROD_DB_HOST,
        port=settings.PROD_DB_PORT,
        user=settings.PROD_DB_USERNAME,
        password=settings.PROD_DB_PASSWORD,
        database=settings.PROD_DB_DATABASE,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=15,
        charset="utf8mb4",
    )


def _stringify_dates(row: dict, date_columns: tuple[str, ...]) -> dict:
    """Cast date/datetime column values to str so downstream seed_loader
    functions (which expect string-like values, per the CSV parsing path
    they were built for) receive the same shape either way."""
    converted = dict(row)
    for col in date_columns:
        value = converted.get(col)
        if value is not None:
            converted[col] = str(value)
    return converted


def fetch_fq_movie_master_count() -> int:
    """SELECT COUNT(*) FROM fq_movie_master — used to populate the sync job's
    `total` up front, since the row-fetch generator's length is otherwise
    unknown until fully exhausted."""
    conn = _prod_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS cnt FROM fq_movie_master")
            row = cursor.fetchone()
            return int(row["cnt"])
    finally:
        conn.close()


def fetch_fq_movie_master_intl_count() -> int:
    """SELECT COUNT(*) FROM fq_movie_master_intl — same purpose as
    fetch_fq_movie_master_count(), for the international table."""
    conn = _prod_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS cnt FROM fq_movie_master_intl")
            row = cursor.fetchone()
            return int(row["cnt"])
    finally:
        conn.close()


def fetch_fq_movie_master_rows() -> Iterator[dict]:
    """Yield fq_movie_master rows as dicts shaped for seed_loader.seed_from_rows.

    Fetches in batches of _FETCH_BATCH_SIZE via cursor.fetchmany() rather than
    fetchall() — this table alone is ~46K rows, and the international table
    fetch below reuses this same pattern at ~158K rows. The connection is
    closed via try/finally (not "close after the loop exhausts naturally") so
    it doesn't leak if the caller raises or stops iterating early.
    """
    conn = _prod_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT {', '.join(_DOMESTIC_COLUMNS)} FROM fq_movie_master")
            while True:
                batch = cursor.fetchmany(_FETCH_BATCH_SIZE)
                if not batch:
                    break
                for row in batch:
                    yield _stringify_dates(row, ("release_date",))
    finally:
        conn.close()


def fetch_fq_movie_master_intl_rows() -> Iterator[dict]:
    """Yield fq_movie_master_intl rows as dicts shaped for
    seed_loader.seed_intl_from_rows. See fetch_fq_movie_master_rows() for the
    batching/cleanup rationale — identical here."""
    conn = _prod_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT {', '.join(_INTL_COLUMNS)} FROM fq_movie_master_intl")
            while True:
                batch = cursor.fetchmany(_FETCH_BATCH_SIZE)
                if not batch:
                    break
                for row in batch:
                    yield _stringify_dates(row, ("release_date", "updated_on"))
    finally:
        conn.close()
