"""drop circuitoverride table

Revision ID: a1b2c3d4e5f6
Revises: 2153535df54c
Create Date: 2026-07-05 23:00:00.000000

Circuit overrides are now represented as AmenityMapping rows with
circuit_name set, making the circuitoverride table redundant.
"""
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = '2153535df54c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index('ix_circuitoverride_keyword', table_name='circuitoverride')
    op.drop_index('ix_circuitoverride_circuit_name', table_name='circuitoverride')
    op.drop_table('circuitoverride')


def downgrade() -> None:
    import sqlalchemy as sa
    import sqlmodel
    op.create_table(
        'circuitoverride',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('keyword', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('circuit_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('screen_format', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('na_default', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_circuitoverride_keyword', 'circuitoverride', ['keyword'])
    op.create_index('ix_circuitoverride_circuit_name', 'circuitoverride', ['circuit_name'])
