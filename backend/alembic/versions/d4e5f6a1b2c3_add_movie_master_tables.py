"""add movie master tables

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4e5f6a1b2c3'
down_revision = 'c3d4e5f6a1b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'moviemaster',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('movie_title', sa.String(), nullable=False),
        sa.Column('release_date', sa.String(), nullable=True),
        sa.Column('imdb_id', sa.String(), nullable=True),
        sa.Column('cover_image', sa.String(), nullable=True),
        sa.Column('director', sa.String(), nullable=True),
        sa.Column('cast_list', sa.String(), nullable=True),
        sa.Column('running_time', sa.Integer(), nullable=True),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('search_tags', sa.String(), nullable=True),
        sa.Column('title_tag', sa.String(), nullable=True),
        sa.Column('short_name', sa.String(), nullable=True),
        sa.Column('cover_image_phash', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_moviemaster_movie_title', 'moviemaster', ['movie_title'])

    op.create_table(
        'movietitlealias',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('normalized_alias', sa.String(), nullable=False),
        sa.Column('country_code', sa.String(), nullable=True),
        sa.Column('movie_master_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(), nullable=False, server_default='human'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['movie_master_id'], ['moviemaster.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_movietitlealias_normalized_alias', 'movietitlealias', ['normalized_alias'])


def downgrade() -> None:
    op.drop_index('ix_movietitlealias_normalized_alias', 'movietitlealias')
    op.drop_table('movietitlealias')
    op.drop_index('ix_moviemaster_movie_title', 'moviemaster')
    op.drop_table('moviemaster')
