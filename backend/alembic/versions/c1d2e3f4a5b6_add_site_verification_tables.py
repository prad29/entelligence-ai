"""add deleted-showtimes site-verification tables + job counters

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-09-23

Adds the schema for the Deleted Showtimes site-verification step (see
docs/plans/2026-09-23-deleted-showtimes-site-verification-design.md):

* `deletedshowtimejob` gains `site_verified_count`, `site_wrong_count`,
  `site_unavailable_count`, `scrape_credits_used` — additive counters, no
  backfill needed (default 0 is correct for every pre-existing job, none of
  which ever ran a site check).
* New tables `theatersiteurl` (persistent AMC-style URL cache, generalized to
  any adapter), `scrapedotokenslot` (token rotation state, structural copy of
  `serpapikeyslot`), `scrapedocalllog` (call log, structural copy of
  `serpapicalllog`).

Every op is guarded (has_table / column-existence check) so a schema already
created by SQLModel's create_all() at FastAPI startup upgrades cleanly,
matching f1a2b3c4d5e6_add_serpapikeyslot_table.py / b3c4d5e6f7a8's convention.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c1d2e3f4a5b6'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def _column_names(inspector, table_name):
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_cols = _column_names(inspector, "deletedshowtimejob")
    if "site_verified_count" not in existing_cols:
        op.add_column(
            "deletedshowtimejob",
            sa.Column("site_verified_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if "site_wrong_count" not in existing_cols:
        op.add_column(
            "deletedshowtimejob",
            sa.Column("site_wrong_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if "site_unavailable_count" not in existing_cols:
        op.add_column(
            "deletedshowtimejob",
            sa.Column("site_unavailable_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if "scrape_credits_used" not in existing_cols:
        op.add_column(
            "deletedshowtimejob",
            sa.Column("scrape_credits_used", sa.Integer(), nullable=False, server_default="0"),
        )

    if not inspector.has_table("theatersiteurl"):
        op.create_table(
            "theatersiteurl",
            sa.Column("theater_name", sa.String(), nullable=False),
            sa.Column("circuit_name", sa.String(), nullable=False),
            sa.Column("resolved_url", sa.String(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(), nullable=False),
            sa.Column("source", sa.String(), nullable=False, server_default="serpapi"),
            sa.PrimaryKeyConstraint("theater_name"),
        )

    if not inspector.has_table("scrapedotokenslot"):
        op.create_table(
            "scrapedotokenslot",
            sa.Column("slot", sa.Integer(), nullable=False),
            sa.Column("key_fingerprint", sa.String(), nullable=False),
            sa.Column("exhausted_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.String(), nullable=True),
            sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("slot"),
        )

    if not inspector.has_table("scrapedocalllog"):
        op.create_table(
            "scrapedocalllog",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("ts", sa.DateTime(), nullable=False),
            sa.Column("job_id", sa.String(), nullable=True),
            sa.Column("slot", sa.Integer(), nullable=False),
            sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("credits_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_type", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_scrapedocalllog_ts", "scrapedocalllog", ["ts"])
        op.create_index("ix_scrapedocalllog_job_id", "scrapedocalllog", ["job_id"])
        op.create_index("ix_scrapedocalllog_slot", "scrapedocalllog", ["slot"])


def downgrade() -> None:
    op.drop_table("scrapedocalllog")
    op.drop_table("scrapedotokenslot")
    op.drop_table("theatersiteurl")
    op.drop_column("deletedshowtimejob", "scrape_credits_used")
    op.drop_column("deletedshowtimejob", "site_unavailable_count")
    op.drop_column("deletedshowtimejob", "site_wrong_count")
    op.drop_column("deletedshowtimejob", "site_verified_count")
