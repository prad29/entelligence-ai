"""
CLI entry points for the Amenity Screen Format Detector backend.

Usage:
    python app/cli.py seed-from-xlsx path/to/Amenities_Priority.xlsx
"""

import typer

from app.detection.seed_loader import seed_db
from app.database import create_db_and_tables, engine as db_engine
from sqlmodel import Session

cli = typer.Typer(help="Amenity Screen Format Detector CLI")


@cli.command()
def seed_from_xlsx(
    path: str = typer.Argument(..., help="Path to Amenities Priority.xlsx"),
) -> None:
    """
    Parse the xlsx file and upsert all mapping rows into the database.

    Ensures all tables exist before seeding.  Safe to re-run — rows are
    merged (upserted) by primary key so no duplicates are created.
    """
    create_db_and_tables()
    with Session(db_engine) as session:
        seed_db(session, path)
    typer.echo(f"Seeded from {path}")


@cli.command()
def seed_movie_master(
    path: str = typer.Argument(..., help="Path to movie_master.csv"),
    reset: bool = typer.Option(False, "--reset", help="Truncate table before seeding"),
) -> None:
    """
    Seed the moviemaster table from a CSV dump.

    Safe to re-run without --reset; rows are upserted by id.
    Use --reset to truncate and reload from scratch.
    """
    create_db_and_tables()
    from app.title_matching.seed_loader import seed_movie_master as _seed
    with Session(db_engine) as session:
        total = _seed(session, path, reset=reset)
    typer.echo(f"Done: {total} rows seeded from {path}")

    # Queue semantic index build after seeding
    try:
        from app.tasks.semantic_tasks import build_semantic_index_task
        task = build_semantic_index_task.delay()
        typer.echo(f"Vespa semantic index build queued (task: {task.id})")
    except Exception as exc:
        typer.echo(f"Warning: could not queue semantic index build: {exc}")


@cli.command()
def seed_movie_master_intl(
    path: str = typer.Argument(..., help="Path to Movie Master International Dump.csv"),
    reset: bool = typer.Option(False, "--reset", help="Truncate table before seeding"),
) -> None:
    """
    Seed the moviemasterintl table from the international CSV dump.

    Grain is (movie_id, country, release_date) — unlike seed-movie-master,
    rows are never collapsed by movie_id alone. Safe to re-run without
    --reset; rows are upserted by the (movie_id, country, release_date) triple.
    """
    create_db_and_tables()
    from app.title_matching.seed_loader import seed_movie_master_intl as _seed_intl
    with Session(db_engine) as session:
        result = _seed_intl(session, path, reset=reset)
    total = result["inserted"] + result["updated"]
    typer.echo(f"Done: {total} rows seeded from {path}")

    # Queue international semantic index build after seeding
    try:
        from app.tasks.semantic_tasks import build_semantic_index_intl_task
        task = build_semantic_index_intl_task.delay()
        typer.echo(f"Vespa international semantic index build queued (task: {task.id})")
    except Exception as exc:
        typer.echo(f"Warning: could not queue international semantic index build: {exc}")


if __name__ == "__main__":
    cli()
