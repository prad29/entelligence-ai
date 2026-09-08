"""add genre/synopsis to moviemaster, pipeline_variant to movietitlebatchjob

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-09-08

Domestic agentic title-match v2 (metadata-aware matching + incomplete-
metadata guardrail): the pre-fetched candidate payload sent to Claude needs
`genre`/`synopsis` alongside the existing `director`/`cast_list` columns, and
the shared batch job table needs a discriminator so v1/v2 batch jobs can be
told apart without forking the cross-pipeline scheduler onto a second table.

All three columns are nullable with no server_default and no backfill --
`ADD COLUMN ... NULL` with no default is metadata-only in PG 11+ (no table
rewrite, brief lock). Existing ~46K moviemaster rows read back NULL for
genre/synopsis until the next production sync; existing movietitlebatchjob
rows read back NULL for pipeline_variant, which is exactly "v1".

Each add_column is individually guarded by a column-existence check, since
SQLModel's create_all() at FastAPI startup may already have added these
columns on a fresh DB before this migration runs (same defensive pattern as
b3c4d5e6f7a8_add_dispatch_finalize_columns.py).
"""
from alembic import op
import sqlalchemy as sa

revision = 'f7a8b9c0d1e2'
down_revision = 'e6f7a8b9c0d1'
branch_labels = None
depends_on = None


def _column_names(inspector, table_name):
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    moviemaster_cols = _column_names(inspector, "moviemaster")
    if "genre" not in moviemaster_cols:
        op.add_column("moviemaster", sa.Column("genre", sa.String(), nullable=True))
    if "synopsis" not in moviemaster_cols:
        op.add_column("moviemaster", sa.Column("synopsis", sa.String(), nullable=True))

    job_cols = _column_names(inspector, "movietitlebatchjob")
    if "pipeline_variant" not in job_cols:
        op.add_column(
            "movietitlebatchjob", sa.Column("pipeline_variant", sa.String(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("movietitlebatchjob", "pipeline_variant")
    op.drop_column("moviemaster", "synopsis")
    op.drop_column("moviemaster", "genre")
