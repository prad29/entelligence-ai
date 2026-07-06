"""add audit_mode to detectionjob and movieformatjob

Revision ID: c3d4e5f6a1b2
Revises: b2c3d4e5f6a1
Create Date: 2026-07-06

"""
from alembic import op
import sqlalchemy as sa

revision = 'c3d4e5f6a1b2'
down_revision = 'b2c3d4e5f6a1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('detectionjob', sa.Column('audit_mode', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('movieformatjob', sa.Column('audit_mode', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('detectionjob', 'audit_mode')
    op.drop_column('movieformatjob', 'audit_mode')
