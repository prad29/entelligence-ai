"""add dispatch/finalize columns for agentic batch fairness (phase 4)

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-08-26

Adds the schema Phase 4 of the agentic-batch-concurrency plan needs for
counter-based finalize detection, while the existing Celery chord stays the
active dispatch/completion mechanism this phase (belt-and-suspenders — see
local-docs/2026-08-25-agentic-batch-concurrency-design.md and the
implementation plan's Phase 4 section):

* `movietitlebatchjob`/`movietitleintlbatchjob` gain `dispatched` (unused
  until Phase 5's windowed dispatcher) and `finalize_claimed_at` (the
  counter-based finalize claim).
* `apititlematchjob` gains only `finalize_claimed_at` — external already has
  per-row `ApiTitleMatchRow.status` as its dispatch state, so there is no
  cursor to add there.

Each add_column is individually guarded by an explicit column-existence
check so a partially create_all()-ed schema (SQLModel metadata already
having added these columns at FastAPI startup) upgrades cleanly, following
the same defensive pattern as f1a2b3c4d5e6_add_serpapikeyslot_table.py /
a2b3c4d5e6f7_add_observability_tables.py (which guard on `has_table`; this
migration needs the column-level equivalent since it's adding columns to
existing tables, not creating new ones).

MANDATORY BACKFILL (finding #4 in the plan, not optional): any job already
`processing` when this migration runs had 100% of its rows already pushed
to Celery by the pre-existing chord. Leaving `dispatched` at its default of
0 for those jobs would make a *future* Phase 5 round-robin dispatcher think
none of their rows were ever sent, and re-dispatch the whole job --
double-processing every row. Backfilling `dispatched = total` for
`status = 'processing'` jobs now means Phase 5 inherits a consistent
starting state with no migration of its own required.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b3c4d5e6f7a8'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None


def _column_names(inspector, table_name):
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "dispatched" not in _column_names(inspector, "movietitlebatchjob"):
        op.add_column(
            "movietitlebatchjob",
            sa.Column("dispatched", sa.Integer(), nullable=False, server_default="0"),
        )
    if "finalize_claimed_at" not in _column_names(inspector, "movietitlebatchjob"):
        op.add_column(
            "movietitlebatchjob",
            sa.Column("finalize_claimed_at", sa.DateTime(), nullable=True),
        )

    if "dispatched" not in _column_names(inspector, "movietitleintlbatchjob"):
        op.add_column(
            "movietitleintlbatchjob",
            sa.Column("dispatched", sa.Integer(), nullable=False, server_default="0"),
        )
    if "finalize_claimed_at" not in _column_names(inspector, "movietitleintlbatchjob"):
        op.add_column(
            "movietitleintlbatchjob",
            sa.Column("finalize_claimed_at", sa.DateTime(), nullable=True),
        )

    if "finalize_claimed_at" not in _column_names(inspector, "apititlematchjob"):
        op.add_column(
            "apititlematchjob",
            sa.Column("finalize_claimed_at", sa.DateTime(), nullable=True),
        )

    # Mandatory backfill (finding #4) -- see module docstring. Idempotent to
    # re-run: a job already backfilled to dispatched=total, or one whose
    # dispatched already equals total for any other reason, is a harmless
    # no-op update.
    op.execute("UPDATE movietitlebatchjob SET dispatched = total WHERE status = 'processing'")
    op.execute("UPDATE movietitleintlbatchjob SET dispatched = total WHERE status = 'processing'")


def downgrade() -> None:
    op.drop_column("apititlematchjob", "finalize_claimed_at")
    op.drop_column("movietitleintlbatchjob", "finalize_claimed_at")
    op.drop_column("movietitleintlbatchjob", "dispatched")
    op.drop_column("movietitlebatchjob", "finalize_claimed_at")
    op.drop_column("movietitlebatchjob", "dispatched")
