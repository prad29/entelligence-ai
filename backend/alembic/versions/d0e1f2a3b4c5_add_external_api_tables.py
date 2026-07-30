"""add external api tables (apikey, apititlematchjob, apititlematchrow)

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa

revision = 'd0e1f2a3b4c5'
down_revision = 'c9d0e1f2a3b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('apikey'):
        op.create_table(
            'apikey',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('key_hash', sa.String(), nullable=False),
            sa.Column('key_prefix', sa.String(), nullable=False),
            sa.Column('label', sa.String(), nullable=True),
            sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('max_rows_per_batch', sa.Integer(), nullable=True),
            sa.Column('max_concurrent_jobs', sa.Integer(), nullable=False, server_default='5'),
            sa.Column('requests_per_minute', sa.Integer(), nullable=False, server_default='60'),
            sa.Column('db_update_allowed', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('rotated_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_apikey_key_hash', 'apikey', ['key_hash'], unique=True)

    if not inspector.has_table('apititlematchjob'):
        op.create_table(
            'apititlematchjob',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('api_key_id', sa.String(), nullable=False),
            sa.Column('market', sa.String(), nullable=False),
            sa.Column('db_update', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('phase', sa.String(), nullable=False, server_default='queued'),
            sa.Column('rows_total', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('rows_processed', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('rows_matched', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('rows_no_match', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('rows_failed', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('error', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('started_at', sa.DateTime(), nullable=True),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.Column('ttl', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['api_key_id'], ['apikey.id']),
        )
        op.create_index('ix_apititlematchjob_api_key_id', 'apititlematchjob', ['api_key_id'])

    if not inspector.has_table('apititlematchrow'):
        op.create_table(
            'apititlematchrow',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('job_id', sa.String(), nullable=False),
            sa.Column('row_uuid', sa.String(), nullable=False),
            sa.Column('input_json', sa.String(), nullable=False),
            sa.Column('status', sa.String(), nullable=False, server_default='pending'),
            sa.Column('mapped_title', sa.String(), nullable=True),
            sa.Column('confidence', sa.Float(), nullable=True),
            sa.Column('reasoning', sa.String(), nullable=True),
            sa.Column('present_in_db', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('error', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['job_id'], ['apititlematchjob.id']),
            sa.UniqueConstraint('job_id', 'row_uuid', name='uq_api_row_job_uuid'),
        )
        op.create_index('ix_apititlematchrow_job_id', 'apititlematchrow', ['job_id'])
        op.create_index('ix_apititlematchrow_row_uuid', 'apititlematchrow', ['row_uuid'])


def downgrade() -> None:
    op.drop_index('ix_apititlematchrow_row_uuid', 'apititlematchrow')
    op.drop_index('ix_apititlematchrow_job_id', 'apititlematchrow')
    op.drop_table('apititlematchrow')

    op.drop_index('ix_apititlematchjob_api_key_id', 'apititlematchjob')
    op.drop_table('apititlematchjob')

    op.drop_index('ix_apikey_key_hash', 'apikey')
    op.drop_table('apikey')
