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


# ─────────────────────────────────────────────────────────────────────────────
# International Movie Master — grain (movie_id, country, release_date).
#
# Deliberately separate from _upsert_rows/seed_movie_master above: the
# domestic loader collapses rows by picking a single id column and upserting
# on it alone, which would silently overwrite per-country data if reused
# here unmodified. This path upserts on the (movie_id, country, release_date)
# triple instead, so the source dump's true per-market grain is preserved.
# ─────────────────────────────────────────────────────────────────────────────

_INTL_MOVIE_ID_ALIASES = ("movie_id", "movie_id ")
_INTL_SOURCE_ROW_ID_ALIASES = ("id",)
_INTL_TITLE_ALIASES = ("movie_title", "title")
_INTL_MASTER_TITLE_ALIASES = ("master_movie_title",)
_INTL_COUNTRY_ALIASES = ("country",)
_INTL_COUNTRY_ID_ALIASES = ("country_id",)
_INTL_RELEASE_DATE_ALIASES = ("release_date",)
_INTL_STUDIO_ALIASES = ("studio",)
_INTL_RATING_ALIASES = ("rating",)
_INTL_GENRE_ALIASES = ("genre",)
_INTL_GENRE2_ALIASES = ("genre2",)
_INTL_RUNTIME_ALIASES = ("running_time",)
_INTL_UPDATED_ON_ALIASES = ("updated_on",)

_NULL_LIKE_STRINGS = {"null", "undefined", ""}


def _clean_intl(value: Optional[str]) -> Optional[str]:
    """Coerce the CSV's literal 'null'/'undefined'/blank string sentinels to None."""
    if value is None:
        return None
    stripped = str(value).strip()
    if stripped.lower() in _NULL_LIKE_STRINGS:
        return None
    return stripped


def _pick_intl(row: dict, aliases: tuple[str, ...]) -> Optional[str]:
    for alias in aliases:
        if alias in row:
            cleaned = _clean_intl(row[alias])
            if cleaned is not None:
                return cleaned
    return None


def seed_intl_from_rows(session: Session, rows: list[dict]) -> dict:
    """Upsert international master rows keyed on (movie_id, country, release_date)."""
    from app.models import MovieMasterIntl

    inserted = 0
    updated = 0
    skipped = 0
    skipped_undefined_country = 0

    for row in rows:
        raw_movie_id = _pick_intl(row, _INTL_MOVIE_ID_ALIASES)
        raw_title = _pick_intl(row, _INTL_TITLE_ALIASES)
        raw_country = _pick_intl(row, _INTL_COUNTRY_ALIASES)

        if raw_country is None:
            # country is part of the unique grain and NOT-NULL — the CSV's
            # literal "undefined"/blank country rows are unusable here.
            skipped_undefined_country += 1
            continue

        if not raw_movie_id or not raw_title:
            skipped += 1
            continue

        movie_id = _to_int(raw_movie_id)
        if movie_id is None:
            skipped += 1
            continue

        release_date = _pick_intl(row, _INTL_RELEASE_DATE_ALIASES)

        existing = session.exec(
            _select_intl(MovieMasterIntl, movie_id, raw_country, release_date)
        ).first()

        source_row_id = _to_int(_pick_intl(row, _INTL_SOURCE_ROW_ID_ALIASES))
        master_movie_title = _pick_intl(row, _INTL_MASTER_TITLE_ALIASES)
        country_id = _to_int(_pick_intl(row, _INTL_COUNTRY_ID_ALIASES))
        studio = _pick_intl(row, _INTL_STUDIO_ALIASES)
        rating = _pick_intl(row, _INTL_RATING_ALIASES)
        genre = _pick_intl(row, _INTL_GENRE_ALIASES)
        genre2 = _pick_intl(row, _INTL_GENRE2_ALIASES)
        running_time = _to_int(_pick_intl(row, _INTL_RUNTIME_ALIASES))
        updated_on = _pick_intl(row, _INTL_UPDATED_ON_ALIASES)

        if existing is not None:
            existing.source_row_id = source_row_id
            existing.movie_title = raw_title
            existing.master_movie_title = master_movie_title
            existing.country_id = country_id
            existing.studio = studio
            existing.rating = rating
            existing.genre = genre
            existing.genre2 = genre2
            existing.running_time = running_time
            existing.updated_on = updated_on
            session.add(existing)
            updated += 1
        else:
            record = MovieMasterIntl(
                source_row_id=source_row_id,
                movie_id=movie_id,
                movie_title=raw_title,
                master_movie_title=master_movie_title,
                country=raw_country,
                country_id=country_id,
                release_date=release_date,
                studio=studio,
                rating=rating,
                genre=genre,
                genre2=genre2,
                running_time=running_time,
                updated_on=updated_on,
            )
            session.add(record)
            inserted += 1

        if (inserted + updated) % 5000 == 0:
            session.commit()

    session.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "skipped_undefined_country": skipped_undefined_country,
    }


def _select_intl(model, movie_id: int, country: str, release_date: Optional[str]):
    from sqlmodel import select

    return select(model).where(
        model.movie_id == movie_id,
        model.country == country,
        model.release_date == release_date,
    )


def seed_movie_master_intl(session: Session, path: str, reset: bool = False) -> dict:
    """Seed the international master table from a CSV file path (used by the CLI)."""
    if reset:
        from sqlalchemy import inspect as sa_inspect
        inspector = sa_inspect(session.get_bind())
        if inspector.has_table("moviemasterintl"):
            session.exec(sa_text("DELETE FROM moviemasterintl"))
        session.commit()

    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    result = seed_intl_from_rows(session, rows)
    typer.echo(
        f"  inserted={result['inserted']} updated={result['updated']} "
        f"skipped={result['skipped']} skipped_undefined_country={result['skipped_undefined_country']}"
    )
    return result
