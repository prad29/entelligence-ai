"""add lobby check tables (lobbycheckjob, lobbycheckrow)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

revision = 'd5e6f7a8b9c0'
down_revision = 'c4d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('lobbycheckjob'):
        op.create_table(
            'lobbycheckjob',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('api_key_id', sa.String(), nullable=False),
            sa.Column('phase', sa.String(), nullable=False, server_default='queued'),
            sa.Column('rows_total', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('rows_processed', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('rows_succeeded', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('rows_failed', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('rows_needs_review', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('error', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('started_at', sa.DateTime(), nullable=True),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.Column('ttl', sa.DateTime(), nullable=True),
            sa.Column('finalize_claimed_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['api_key_id'], ['apikey.id']),
        )
        op.create_index('ix_lobbycheckjob_api_key_id', 'lobbycheckjob', ['api_key_id'])

    if not inspector.has_table('lobbycheckrow'):
        op.create_table(
            'lobbycheckrow',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('job_id', sa.String(), nullable=False),
            sa.Column('row_uuid', sa.String(), nullable=False),
            sa.Column('image_url', sa.String(), nullable=False),
            sa.Column('input_json', sa.String(), nullable=False),
            sa.Column('status', sa.String(), nullable=False, server_default='pending'),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('error', sa.String(), nullable=True),
            sa.Column('movie_title', sa.String(), nullable=True),
            sa.Column('confidence_movie_title', sa.Float(), nullable=True),
            sa.Column('material_type', sa.String(), nullable=True),
            sa.Column('confidence_material_type', sa.Float(), nullable=True),
            sa.Column('material_quantity', sa.Integer(), nullable=True),
            sa.Column('confidence_material_quantity', sa.Float(), nullable=True),
            sa.Column('material_condition', sa.String(), nullable=True),
            sa.Column('confidence_material_condition', sa.Float(), nullable=True),
            sa.Column('visual_notes', sa.String(), nullable=True),
            sa.Column('defects_json', sa.String(), nullable=True),
            sa.Column('defect_evidence', sa.String(), nullable=True),
            sa.Column('condition_conflict', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('framing', sa.String(), nullable=True),
            sa.Column('model_id', sa.String(), nullable=True),
            sa.Column('input_tokens', sa.Integer(), nullable=True),
            sa.Column('output_tokens', sa.Integer(), nullable=True),
            sa.Column('cost_usd', sa.Float(), nullable=True),
            sa.Column('latency_ms', sa.Integer(), nullable=True),
            sa.Column('parse_retries', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['job_id'], ['lobbycheckjob.id']),
            sa.UniqueConstraint('job_id', 'row_uuid', name='uq_lobby_row_job_uuid'),
        )
        op.create_index('ix_lobbycheckrow_job_id', 'lobbycheckrow', ['job_id'])
        op.create_index('ix_lobbycheckrow_row_uuid', 'lobbycheckrow', ['row_uuid'])


def downgrade() -> None:
    op.drop_index('ix_lobbycheckrow_row_uuid', 'lobbycheckrow')
    op.drop_index('ix_lobbycheckrow_job_id', 'lobbycheckrow')
    op.drop_table('lobbycheckrow')

    op.drop_index('ix_lobbycheckjob_api_key_id', 'lobbycheckjob')
    op.drop_table('lobbycheckjob')
