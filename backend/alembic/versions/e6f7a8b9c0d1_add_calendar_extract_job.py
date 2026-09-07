"""add calendarextractjob table

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

revision = 'e6f7a8b9c0d1'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('calendarextractjob'):
        op.create_table(
            'calendarextractjob',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('status', sa.String(), nullable=False, server_default='queued'),
            sa.Column('original_filename', sa.String(), nullable=True),
            sa.Column('file_hash', sa.String(), nullable=False),
            sa.Column('file_path', sa.String(), nullable=True),
            sa.Column('output_path', sa.String(), nullable=True),
            sa.Column('is_release_calendar', sa.Boolean(), nullable=True),
            sa.Column('classification_reason', sa.String(), nullable=True),
            sa.Column('rows_extracted', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('model_id', sa.String(), nullable=True),
            sa.Column('error', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.Column('ttl', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_calendarextractjob_file_hash', 'calendarextractjob', ['file_hash'])


def downgrade() -> None:
    op.drop_index('ix_calendarextractjob_file_hash', 'calendarextractjob')
    op.drop_table('calendarextractjob')
