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


if __name__ == "__main__":
    cli()
