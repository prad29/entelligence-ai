"""add movie format tables

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-07-06
"""
from alembic import op
import sqlalchemy as sa

revision = 'b2c3d4e5f6a1'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'movieformatmapping',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('keyword', sa.String(), nullable=False),
        sa.Column('format', sa.String(), nullable=False),
        sa.Column('priority_tier', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='approved'),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('created_by', sa.String(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_movieformatmapping_keyword', 'movieformatmapping', ['keyword'])

    op.create_table(
        'movieformatreviewitem',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('payload', sa.String(), nullable=True),
        sa.Column('source_string', sa.String(), nullable=True),
        sa.Column('suggested_format', sa.String(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('reasoning', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('reviewer', sa.String(), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('mapping_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'movieformatjob',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='queued'),
        sa.Column('total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('processed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('file_path', sa.String(), nullable=True),
        sa.Column('output_path', sa.String(), nullable=True),
        sa.Column('include_diagnostics', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('ttl', sa.DateTime(), nullable=True),
        sa.Column('stats', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('movieformatjob')
    op.drop_table('movieformatreviewitem')
    op.drop_index('ix_movieformatmapping_keyword', 'movieformatmapping')
    op.drop_table('movieformatmapping')
