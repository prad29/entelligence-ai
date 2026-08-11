"""add deletedshowtimejob table

Revision ID: 326d2ebe211d
Revises: d0e1f2a3b4c5
Create Date: 2026-08-11

"""
from alembic import op
import sqlalchemy as sa

revision = '326d2ebe211d'
down_revision = 'd0e1f2a3b4c5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('deletedshowtimejob'):
        # Table already created by create_db_and_tables (SQLModel metadata
        # create_all) at FastAPI startup — this migration is a no-op.
        return

    op.create_table(
        'deletedshowtimejob',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='queued'),
        sa.Column('total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('processed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('true_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('false_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('unknown_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('consecutive_failures', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('aborted', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('title_missing_is_deleted', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('strict_screen_count', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('theater_verify', sa.String(), nullable=False, server_default='warn'),
        sa.Column('fallback', sa.String(), nullable=False, server_default='auto'),
        sa.Column('workers', sa.Integer(), nullable=False, server_default='4'),
        sa.Column('file_path', sa.String(), nullable=True),
        sa.Column('output_path', sa.String(), nullable=True),
        sa.Column('audit_output_path', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('ttl', sa.DateTime(), nullable=True),
        sa.Column('original_filename', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('deletedshowtimejob')
