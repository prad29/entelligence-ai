import csv
import io
from typing import Optional

import typer
from sqlalchemy import text as sa_text
from sqlmodel import Session


_ID_ALIASES = ("id", "movie_id", "ID")
_TITLE_ALIASES = ("movie_title", "title", "Title", "name")
_RELEASE_DATE_ALIASES = ("release_date", "ReleaseDate", "release date")
_IMDB_ALIASES = ("imdb_id", "imdb", "IMDB_ID")
_COVER_ALIASES = ("cover_image", "poster", "image_url", "CoverImage")
_DIRECTOR_ALIASES = ("director", "Director")
_CAST_ALIASES = ("cast", "Cast", "cast_list")
_RUNTIME_ALIASES = ("running_time", "runtime", "RunningTime", "duration")
_PARENT_ALIASES = ("parent_id", "ParentID")
_TAGS_ALIASES = ("search_tags", "SearchTags", "tags")
_TITLE_TAG_ALIASES = ("title_tag", "TitleTag")
_SHORT_NAME_ALIASES = ("short_name", "ShortName")


def _pick(row: dict, aliases: tuple[str, ...]) -> Optional[str]:
    for alias in aliases:
        if alias in row and row[alias] not in ("", None):
            return row[alias]
    return None


def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _upsert_rows(session: Session, rows: list[dict]) -> dict:
    from app.models import MovieMaster

    inserted = 0
    updated = 0
    skipped = 0

    for i, row in enumerate(rows, start=1):
        raw_id = _pick(row, _ID_ALIASES)
        raw_title = _pick(row, _TITLE_ALIASES)

        if not raw_id or not str(raw_id).strip() or not raw_title or not str(raw_title).strip():
            skipped += 1
            continue

        row_id = _to_int(raw_id)
        if row_id is None:
            skipped += 1
            continue

        existing = session.get(MovieMaster, row_id)
        if existing is not None:
            existing.movie_title = str(raw_title).strip()
            existing.release_date = _pick(row, _RELEASE_DATE_ALIASES)
            existing.imdb_id = _pick(row, _IMDB_ALIASES)
            existing.cover_image = _pick(row, _COVER_ALIASES)
            existing.director = _pick(row, _DIRECTOR_ALIASES)
            existing.cast_list = _pick(row, _CAST_ALIASES)
            existing.running_time = _to_int(_pick(row, _RUNTIME_ALIASES))
            existing.parent_id = _to_int(_pick(row, _PARENT_ALIASES))
            existing.search_tags = _pick(row, _TAGS_ALIASES)
            existing.title_tag = _pick(row, _TITLE_TAG_ALIASES)
            existing.short_name = _pick(row, _SHORT_NAME_ALIASES)
            session.add(existing)
            updated += 1
        else:
            record = MovieMaster(
                id=row_id,
                movie_title=str(raw_title).strip(),
                release_date=_pick(row, _RELEASE_DATE_ALIASES),
                imdb_id=_pick(row, _IMDB_ALIASES),
                cover_image=_pick(row, _COVER_ALIASES),
                director=_pick(row, _DIRECTOR_ALIASES),
                cast_list=_pick(row, _CAST_ALIASES),
                running_time=_to_int(_pick(row, _RUNTIME_ALIASES)),
                parent_id=_to_int(_pick(row, _PARENT_ALIASES)),
                search_tags=_pick(row, _TAGS_ALIASES),
                title_tag=_pick(row, _TITLE_TAG_ALIASES),
                short_name=_pick(row, _SHORT_NAME_ALIASES),
            )
            session.add(record)
            inserted += 1

        if (inserted + updated) % 5000 == 0:
            session.commit()

    session.commit()
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def seed_from_rows(session: Session, rows: list[dict]) -> dict:
    """Seed from a list of dicts (used by the API upload endpoint)."""
    return _upsert_rows(session, rows)


def seed_movie_master(session: Session, path: str, reset: bool = False) -> int:
    """Seed from a CSV file path (used by the CLI)."""
    if reset:
        from sqlalchemy import inspect as sa_inspect
        inspector = sa_inspect(session.get_bind())
        if inspector.has_table("movietitlealias"):
            session.exec(sa_text("DELETE FROM movietitlealias"))
        if inspector.has_table("moviemaster"):
            session.exec(sa_text("DELETE FROM moviemaster"))
        session.commit()

    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    result = _upsert_rows(session, rows)
    total = result["inserted"] + result["updated"]
    typer.echo(f"  inserted={result['inserted']} updated={result['updated']} skipped={result['skipped']}")
    return total
