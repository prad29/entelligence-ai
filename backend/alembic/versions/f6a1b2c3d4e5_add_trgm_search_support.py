"""enable pg_trgm/unaccent and add trigram index for movie title search

Revision ID: f6a1b2c3d4e5
Revises: e5f6a1b2c3d4
Create Date: 2026-07-15

"""
from alembic import op

revision = 'f6a1b2c3d4e5'
down_revision = 'e5f6a1b2c3d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    op.execute('CREATE EXTENSION IF NOT EXISTS unaccent')
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_moviemaster_movie_title_trgm '
        'ON moviemaster USING gin (movie_title gin_trgm_ops)'
    )


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ix_moviemaster_movie_title_trgm')
    op.execute('DROP EXTENSION IF EXISTS unaccent')
    op.execute('DROP EXTENSION IF EXISTS pg_trgm')
