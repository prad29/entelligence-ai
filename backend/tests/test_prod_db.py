"""
Unit tests for the production MySQL DB client (backend/app/title_matching/prod_db.py),
used by the "Sync from Production DB" feature. Mocks the pymysql connection so
these run fully offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.title_matching import prod_db


def _mock_connection(batches: list[list[dict]]):
    """Fake pymysql connection whose cursor.fetchmany() dispenses the given
    batches in order, then returns [] to signal exhaustion."""
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)

    remaining = list(batches)

    def _fetchmany(size):
        return remaining.pop(0) if remaining else []

    cursor.fetchmany.side_effect = _fetchmany

    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


class TestRequireProdDbHost:
    def test_raises_when_host_empty(self, monkeypatch):
        monkeypatch.setattr(prod_db.settings, "PROD_DB_HOST", "")
        with pytest.raises(RuntimeError, match="PROD_DB_HOST is not configured"):
            prod_db._require_prod_db_host()

    def test_no_raise_when_host_set(self, monkeypatch):
        monkeypatch.setattr(prod_db.settings, "PROD_DB_HOST", "some-host")
        prod_db._require_prod_db_host()  # must not raise


class TestFetchFqMovieMasterRows:
    def test_query_text_and_batching(self, monkeypatch):
        conn, cursor = _mock_connection([
            [{"id": 1, "movie_title": "A", "release_date": None, "cover_image": None,
              "director": None, "cast": None, "running_time": None, "parent_id": None,
              "search_tags": None, "title_tag": None, "short_name": None}],
            [{"id": 2, "movie_title": "B", "release_date": None, "cover_image": None,
              "director": None, "cast": None, "running_time": None, "parent_id": None,
              "search_tags": None, "title_tag": None, "short_name": None}],
        ])
        monkeypatch.setattr(prod_db, "_prod_db_connection", lambda: conn)

        rows = list(prod_db.fetch_fq_movie_master_rows())

        assert [r["id"] for r in rows] == [1, 2]
        executed_sql = cursor.execute.call_args[0][0]
        assert "FROM fq_movie_master" in executed_sql
        assert "`cast`" in executed_sql
        # fetchmany called in a loop, not fetchall
        assert cursor.fetchmany.call_count > 1
        conn.close.assert_called_once()

    def test_release_date_converted_to_str(self, monkeypatch):
        import datetime

        conn, _cursor = _mock_connection([
            [{"id": 1, "movie_title": "A", "release_date": datetime.date(2020, 1, 1),
              "cover_image": None, "director": None, "cast": None, "running_time": None,
              "parent_id": None, "search_tags": None, "title_tag": None, "short_name": None}],
        ])
        monkeypatch.setattr(prod_db, "_prod_db_connection", lambda: conn)

        rows = list(prod_db.fetch_fq_movie_master_rows())

        assert rows[0]["release_date"] == "2020-01-01"
        assert isinstance(rows[0]["release_date"], str)

    def test_connection_closed_on_early_exit(self, monkeypatch):
        conn, _cursor = _mock_connection([
            [{"id": 1, "movie_title": "A", "release_date": None, "cover_image": None,
              "director": None, "cast": None, "running_time": None, "parent_id": None,
              "search_tags": None, "title_tag": None, "short_name": None}],
            [{"id": 2, "movie_title": "B", "release_date": None, "cover_image": None,
              "director": None, "cast": None, "running_time": None, "parent_id": None,
              "search_tags": None, "title_tag": None, "short_name": None}],
        ])
        monkeypatch.setattr(prod_db, "_prod_db_connection", lambda: conn)

        gen = prod_db.fetch_fq_movie_master_rows()
        next(gen)  # consume one row, leave the generator mid-iteration
        gen.close()  # triggers GeneratorExit -> finally block

        conn.close.assert_called_once()

    def test_connection_closed_when_caller_raises(self, monkeypatch):
        conn, _cursor = _mock_connection([
            [{"id": 1, "movie_title": "A", "release_date": None, "cover_image": None,
              "director": None, "cast": None, "running_time": None, "parent_id": None,
              "search_tags": None, "title_tag": None, "short_name": None}],
            [{"id": 2, "movie_title": "B", "release_date": None, "cover_image": None,
              "director": None, "cast": None, "running_time": None, "parent_id": None,
              "search_tags": None, "title_tag": None, "short_name": None}],
        ])
        monkeypatch.setattr(prod_db, "_prod_db_connection", lambda: conn)

        with pytest.raises(ValueError):
            for _row in prod_db.fetch_fq_movie_master_rows():
                raise ValueError("simulated failure mid-iteration")

        conn.close.assert_called_once()


class TestFetchFqMovieMasterIntlRows:
    def test_query_text_and_conversion(self, monkeypatch):
        import datetime

        conn, cursor = _mock_connection([
            [{"id": 1, "movie_id": 100, "movie_title": "A", "master_movie_title": "A",
              "release_date": datetime.date(2021, 5, 1), "studio": None, "country_id": None,
              "country": "France", "rating": None, "genre": None, "genre2": None,
              "running_time": None, "updated_on": datetime.datetime(2021, 5, 2, 3, 4, 5)}],
        ])
        monkeypatch.setattr(prod_db, "_prod_db_connection", lambda: conn)

        rows = list(prod_db.fetch_fq_movie_master_intl_rows())

        executed_sql = cursor.execute.call_args[0][0]
        assert "FROM fq_movie_master_intl" in executed_sql
        assert rows[0]["release_date"] == "2021-05-01"
        assert rows[0]["updated_on"] == "2021-05-02 03:04:05"
        conn.close.assert_called_once()


class TestFetchCounts:
    def test_fetch_fq_movie_master_count(self, monkeypatch):
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = {"cnt": 45943}
        conn = MagicMock()
        conn.cursor.return_value = cursor
        monkeypatch.setattr(prod_db, "_prod_db_connection", lambda: conn)

        count = prod_db.fetch_fq_movie_master_count()

        assert count == 45943
        assert "COUNT(*)" in cursor.execute.call_args[0][0]
        assert "fq_movie_master" in cursor.execute.call_args[0][0]
        conn.close.assert_called_once()

    def test_fetch_fq_movie_master_intl_count(self, monkeypatch):
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = {"cnt": 157561}
        conn = MagicMock()
        conn.cursor.return_value = cursor
        monkeypatch.setattr(prod_db, "_prod_db_connection", lambda: conn)

        count = prod_db.fetch_fq_movie_master_intl_count()

        assert count == 157561
        assert "fq_movie_master_intl" in cursor.execute.call_args[0][0]
        conn.close.assert_called_once()
