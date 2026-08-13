"""add intlamenitymapping and intldetectionjob tables

Revision ID: e1f2a3b4c5d6
Revises: 326d2ebe211d
Create Date: 2026-08-13

"""
from alembic import op
import sqlalchemy as sa

revision = 'e1f2a3b4c5d6'
down_revision = '326d2ebe211d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('intlamenitymapping'):
        op.create_table(
            'intlamenitymapping',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('amenity_keyword', sa.String(), nullable=False),
            sa.Column('screen_format', sa.String(), nullable=False),
            sa.Column('priority_tier', sa.Integer(), nullable=False),
            sa.Column('circuit_name', sa.String(), nullable=True),
            sa.Column('na_default', sa.String(), nullable=True),
            sa.Column('status', sa.String(), nullable=False, server_default='pending'),
            sa.Column('notes', sa.String(), nullable=True),
            sa.Column('created_by', sa.String(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(
            op.f('ix_intlamenitymapping_amenity_keyword'),
            'intlamenitymapping',
            ['amenity_keyword'],
            unique=False,
        )
        op.create_index(
            op.f('ix_intlamenitymapping_circuit_name'),
            'intlamenitymapping',
            ['circuit_name'],
            unique=False,
        )
    # else: table already created by create_db_and_tables (SQLModel metadata
    # create_all) at FastAPI startup — this migration is a no-op for it.

    if not inspector.has_table('intldetectionjob'):
        op.create_table(
            'intldetectionjob',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('status', sa.String(), nullable=False, server_default='queued'),
            sa.Column('total', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('processed', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('file_path', sa.String(), nullable=True),
            sa.Column('output_path', sa.String(), nullable=True),
            sa.Column('include_diagnostics', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('audit_mode', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('ttl', sa.DateTime(), nullable=True),
            sa.Column('stats', sa.String(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
    # else: table already created by create_db_and_tables (SQLModel metadata
    # create_all) at FastAPI startup — this migration is a no-op for it.


def downgrade() -> None:
    op.drop_index(op.f('ix_intlamenitymapping_circuit_name'), table_name='intlamenitymapping')
    op.drop_index(op.f('ix_intlamenitymapping_amenity_keyword'), table_name='intlamenitymapping')
    op.drop_table('intlamenitymapping')
    op.drop_table('intldetectionjob')
