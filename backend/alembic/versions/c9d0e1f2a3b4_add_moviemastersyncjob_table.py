"""add moviemastersyncjob table

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa

revision = 'c9d0e1f2a3b4'
down_revision = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'moviemastersyncjob',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('market', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='queued'),
        sa.Column('total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('processed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('inserted', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('skipped', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('skipped_undefined_country', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('ttl', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_moviemastersyncjob_market', 'moviemastersyncjob', ['market'])
    op.create_index('ix_moviemastersyncjob_status', 'moviemastersyncjob', ['status'])


def downgrade() -> None:
    op.drop_index('ix_moviemastersyncjob_status', 'moviemastersyncjob')
    op.drop_index('ix_moviemastersyncjob_market', 'moviemastersyncjob')
    op.drop_table('moviemastersyncjob')
