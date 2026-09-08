"""add pipeline_variant to apititlematchjob

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f7
Create Date: 2026-09-09

The external API's new /api/v2/singletitle and /api/v2/batchtitle submit
surface reuses the SAME apititlematchjob/apititlematchrow tables as
/api/v1 (not a second job table) via this nullable discriminator column,
mirroring the domestic and international batch designs
(movietitlebatchjob.pipeline_variant, migration f7a8b9c0d1e2;
movietitleintlbatchjob.pipeline_variant, migration a1b2c3d4e5f7).

A separate table would fork the job/results/retry endpoints, the windowed
dispatcher (external_match_task.enqueue_next_window / scheduler_state) and
the cross-pipeline fairness scheduler, none of which have any v1-vs-v2
behavior difference to justify it. The column keeps all of those at an empty
diff -- external_match_row simply reads job.pipeline_variant, which it
already loads from Postgres on every row.

Nullable, no server_default, no backfill -- existing rows read NULL, which
is exactly "v1".
"""
from alembic import op
import sqlalchemy as sa

revision = 'b1c2d3e4f5a6'
down_revision = 'a1b2c3d4e5f7'
branch_labels = None
depends_on = None


def _column_names(inspector, table_name):
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    job_cols = _column_names(inspector, "apititlematchjob")
    if "pipeline_variant" not in job_cols:
        op.add_column(
            "apititlematchjob", sa.Column("pipeline_variant", sa.String(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("apititlematchjob", "pipeline_variant")
