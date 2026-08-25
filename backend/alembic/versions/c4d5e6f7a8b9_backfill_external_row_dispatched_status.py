"""backfill external row status for in-flight jobs (phase 5)

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-08-26

Data-only migration -- no schema change. `apititlematchrow.status` already
exists as a plain string column; Phase 5 (windowed dispatch + round-robin
top-up) adds a new value it can take, `dispatched`, meaning "claimed and
published to external_match_row, not yet terminal" -- external's per-row
dispatch-state equivalent of domestic/international's integer `dispatched`
cursor column (see b3c4d5e6f7a8, which backfilled THAT cursor for jobs
already `processing` at deploy time).

Finding #4 in the plan applies here too, and Phase 4's migration explicitly
did NOT cover it (external's dispatch-state column didn't exist yet at that
point): any ApiTitleMatchJob already `processing` or `syncing` when Phase 5
ships has rows that are still `status='pending'` in the DB but were, for a
`processing` job, already 100% pushed to Celery by the old chord. Left as
`pending`, Phase 5's round-robin top-up (`enqueue_next_window`) would think
those rows were never dispatched and push them again -- double-processing
every in-flight external row.

Only jobs in `processing` had their pending rows actually pushed by the old
chord -- `syncing` jobs have rows sitting at `pending` deliberately (dispatch
hasn't run yet, per external_dispatch_job's db_update-sync-then-select
ordering), so backfilling those to `dispatched` would be wrong: it would
make Phase 5 think those rows are already in flight when nothing has been
published for them at all, permanently stranding them un-dispatched.
Restricting the backfill to `phase = 'processing'` only avoids that.

Idempotent to re-run: a row already `dispatched`, `completed`, or `failed`
is untouched by the `WHERE status = 'pending'` filter.
"""
from alembic import op

revision = 'c4d5e6f7a8b9'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE apititlematchrow
        SET status = 'dispatched'
        WHERE status = 'pending'
          AND job_id IN (
              SELECT id FROM apititlematchjob WHERE phase = 'processing'
          )
        """
    )


def downgrade() -> None:
    # Reversing this precisely would require knowing which 'dispatched' rows
    # were originally 'pending' at migration time vs. flipped there by real
    # Phase 5 dispatch since -- not recoverable. Downgrading this data-only
    # migration is a no-op; a genuine rollback of Phase 5 code should not
    # need this reversed (rows sitting at 'dispatched' are still handled
    # correctly by the pre-Phase-5 completion predicate, which only cared
    # about 'completed'/'failed').
    pass
