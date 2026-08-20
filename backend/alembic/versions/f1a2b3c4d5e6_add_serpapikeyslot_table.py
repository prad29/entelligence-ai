"""add serpapikeyslot table

Revision ID: f1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-08-20

"""
from alembic import op
import sqlalchemy as sa

revision = 'f1a2b3c4d5e6'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('serpapikeyslot'):
        # Table already created by create_db_and_tables (SQLModel metadata
        # create_all) at FastAPI startup — this migration is a no-op.
        return

    op.create_table(
        'serpapikeyslot',
        sa.Column('slot', sa.Integer(), nullable=False),
        sa.Column('key_fingerprint', sa.String(), nullable=False),
        sa.Column('exhausted_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(), nullable=True),
        sa.Column('failure_count', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('slot'),
    )


def downgrade() -> None:
    op.drop_table('serpapikeyslot')
