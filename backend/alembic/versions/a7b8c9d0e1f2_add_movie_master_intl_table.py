"""add moviemasterintl table

Revision ID: a7b8c9d0e1f2
Revises: f6a1b2c3d4e5
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7b8c9d0e1f2'
down_revision = 'f6a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'moviemasterintl',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_row_id', sa.Integer(), nullable=True),
        sa.Column('movie_id', sa.Integer(), nullable=False),
        sa.Column('movie_title', sa.String(), nullable=False),
        sa.Column('master_movie_title', sa.String(), nullable=True),
        sa.Column('country', sa.String(), nullable=False),
        sa.Column('country_id', sa.Integer(), nullable=True),
        sa.Column('release_date', sa.String(), nullable=True),
        sa.Column('studio', sa.String(), nullable=True),
        sa.Column('rating', sa.String(), nullable=True),
        sa.Column('genre', sa.String(), nullable=True),
        sa.Column('genre2', sa.String(), nullable=True),
        sa.Column('running_time', sa.Integer(), nullable=True),
        sa.Column('updated_on', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('movie_id', 'country', 'release_date', name='uq_intl_movie_country_date'),
    )
    op.create_index('ix_moviemasterintl_source_row_id', 'moviemasterintl', ['source_row_id'])
    op.create_index('ix_moviemasterintl_movie_id', 'moviemasterintl', ['movie_id'])
    op.create_index('ix_moviemasterintl_movie_title', 'moviemasterintl', ['movie_title'])
    op.create_index('ix_moviemasterintl_country', 'moviemasterintl', ['country'])
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_moviemasterintl_movie_title_trgm '
        'ON moviemasterintl USING gin (movie_title gin_trgm_ops)'
    )


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ix_moviemasterintl_movie_title_trgm')
    op.drop_index('ix_moviemasterintl_country', 'moviemasterintl')
    op.drop_index('ix_moviemasterintl_movie_title', 'moviemasterintl')
    op.drop_index('ix_moviemasterintl_movie_id', 'moviemasterintl')
    op.drop_index('ix_moviemasterintl_source_row_id', 'moviemasterintl')
    op.drop_table('moviemasterintl')
