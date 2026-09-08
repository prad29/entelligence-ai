"""add pipeline_variant to movietitleintlbatchjob

Revision ID: a1b2c3d4e5f7
Revises: f7a8b9c0d1e2
Create Date: 2026-09-09

International v2 batch reuses the SAME movietitleintlbatchjob table as v1
(not a third table) via this nullable discriminator column, mirroring the
domestic v2 batch design (movietitlebatchjob.pipeline_variant, migration
f7a8b9c0d1e2). A separate table would need editing the cross-pipeline
fairness scheduler (agentic_scheduler_task.py) in several places or
silently change its fair-share window math; the column keeps that file at
an empty diff.

Nullable, no server_default, no backfill -- existing rows read NULL, which
is exactly "v1".
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f7'
down_revision = 'f7a8b9c0d1e2'
branch_labels = None
depends_on = None


def _column_names(inspector, table_name):
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    job_cols = _column_names(inspector, "movietitleintlbatchjob")
    if "pipeline_variant" not in job_cols:
        op.add_column(
            "movietitleintlbatchjob", sa.Column("pipeline_variant", sa.String(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("movietitleintlbatchjob", "pipeline_variant")
