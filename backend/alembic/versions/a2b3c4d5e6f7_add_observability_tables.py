"""add observability tables

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-08-24

Six tables for the LLM/API usage observability platform (design doc §6).
Each block is individually guarded by inspector.has_table so a partially
create_all()-ed schema upgrades cleanly, following the same defensive
pattern as f1a2b3c4d5e6_add_serpapikeyslot_table.py.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a2b3c4d5e6f7'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('llmcalllog'):
        op.create_table(
            'llmcalllog',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('ts', sa.DateTime(), nullable=False),
            sa.Column('task_type', sa.String(), nullable=False),
            sa.Column('call_path', sa.String(), nullable=False),
            sa.Column('model_id', sa.String(), nullable=False),
            sa.Column('caller_type', sa.String(), nullable=False, server_default='portal'),
            sa.Column('api_key_id', sa.String(), nullable=True),
            sa.Column('job_id', sa.String(), nullable=True),
            sa.Column('job_type', sa.String(), nullable=True),
            sa.Column('market', sa.String(), nullable=True),
            sa.Column('country', sa.String(), nullable=True),
            sa.Column('decision', sa.String(), nullable=True),
            sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('cache_read_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('cache_write_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('cost_usd', sa.Float(), nullable=False, server_default='0'),
            sa.Column('latency_ms', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('status', sa.String(), nullable=False, server_default='success'),
            sa.Column('error_type', sa.String(), nullable=True),
            sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('cache_hit', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_llmcalllog_ts', 'llmcalllog', ['ts'])
        op.create_index('ix_llmcalllog_task_type', 'llmcalllog', ['task_type'])
        op.create_index('ix_llmcalllog_model_id', 'llmcalllog', ['model_id'])
        op.create_index('ix_llmcalllog_caller_type', 'llmcalllog', ['caller_type'])
        op.create_index('ix_llmcalllog_api_key_id', 'llmcalllog', ['api_key_id'])
        op.create_index('ix_llmcalllog_job_id', 'llmcalllog', ['job_id'])
        op.create_index('ix_llmcalllog_market', 'llmcalllog', ['market'])
        op.create_index('ix_llmcalllog_ts_task_type', 'llmcalllog', ['ts', 'task_type'])

    if not inspector.has_table('serpapicalllog'):
        op.create_table(
            'serpapicalllog',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('ts', sa.DateTime(), nullable=False),
            sa.Column('job_id', sa.String(), nullable=True),
            sa.Column('slot', sa.Integer(), nullable=False),
            sa.Column('success', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('calls_made', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('latency_ms', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('error_type', sa.String(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_serpapicalllog_ts', 'serpapicalllog', ['ts'])
        op.create_index('ix_serpapicalllog_job_id', 'serpapicalllog', ['job_id'])
        op.create_index('ix_serpapicalllog_slot', 'serpapicalllog', ['slot'])

    if not inspector.has_table('serpapicreditsnapshot'):
        op.create_table(
            'serpapicreditsnapshot',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('ts', sa.DateTime(), nullable=False),
            sa.Column('slot', sa.Integer(), nullable=False),
            sa.Column('key_fingerprint', sa.String(), nullable=False),
            sa.Column('plan_searches_left', sa.Integer(), nullable=True),
            sa.Column('extra_credits', sa.Integer(), nullable=True),
            sa.Column('total_searches_left', sa.Integer(), nullable=True),
            sa.Column('this_month_usage', sa.Integer(), nullable=True),
            sa.Column('account_email', sa.String(), nullable=True),
            sa.Column('error', sa.String(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_serpapicreditsnapshot_ts', 'serpapicreditsnapshot', ['ts'])
        op.create_index('ix_serpapicreditsnapshot_slot', 'serpapicreditsnapshot', ['slot'])

    if not inspector.has_table('serpercalllog'):
        op.create_table(
            'serpercalllog',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('ts', sa.DateTime(), nullable=False),
            sa.Column('job_id', sa.String(), nullable=True),
            sa.Column('job_type', sa.String(), nullable=True),
            sa.Column('task_type', sa.String(), nullable=True),
            sa.Column('market', sa.String(), nullable=True),
            sa.Column('call_type', sa.String(), nullable=False, server_default='search'),
            sa.Column('success', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('latency_ms', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('error_type', sa.String(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_serpercalllog_ts', 'serpercalllog', ['ts'])
        op.create_index('ix_serpercalllog_job_id', 'serpercalllog', ['job_id'])
        op.create_index('ix_serpercalllog_task_type', 'serpercalllog', ['task_type'])

    if not inspector.has_table('llmusagerolluphourly'):
        op.create_table(
            'llmusagerolluphourly',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('bucket_hour', sa.DateTime(), nullable=False),
            sa.Column('task_type', sa.String(), nullable=False),
            sa.Column('model_id', sa.String(), nullable=False),
            sa.Column('caller_type', sa.String(), nullable=False),
            sa.Column('api_key_id', sa.String(), nullable=False, server_default=''),
            sa.Column('market', sa.String(), nullable=False, server_default=''),
            sa.Column('request_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('cache_hit_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('failure_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('retry_count_sum', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('cache_read_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('cache_write_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('cost_usd', sa.Float(), nullable=False, server_default='0'),
            sa.Column('latency_ms_sum', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint(
                'bucket_hour', 'task_type', 'model_id', 'caller_type', 'api_key_id', 'market',
                name='uq_llm_rollup_hourly_dims',
            ),
        )
        op.create_index('ix_llmusagerolluphourly_bucket_hour', 'llmusagerolluphourly', ['bucket_hour'])

    if not inspector.has_table('llmusagerollupwatermark'):
        op.create_table(
            'llmusagerollupwatermark',
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('last_rolled_id', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('last_rolled_hour', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('name'),
        )


def downgrade() -> None:
    op.drop_table('llmusagerollupwatermark')
    op.drop_table('llmusagerolluphourly')
    op.drop_table('serpercalllog')
    op.drop_table('serpapicreditsnapshot')
    op.drop_table('serpapicalllog')
    op.drop_table('llmcalllog')
