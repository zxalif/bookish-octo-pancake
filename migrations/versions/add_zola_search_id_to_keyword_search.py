"""add zola_search_id to keyword_search

Revision ID: add_zola_search_id
Revises: fd96692c0905
Create Date: 2025-11-09 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_zola_search_id'
down_revision = 'fd96692c0905'  # Latest migration: add_free_plan_to_subscription
branch_labels = None
depends_on = None


def upgrade():
    # Add zola_search_id column to keyword_searches table
    op.add_column('keyword_searches', sa.Column('zola_search_id', sa.String(length=100), nullable=True))
    op.create_index(op.f('ix_keyword_searches_zola_search_id'), 'keyword_searches', ['zola_search_id'], unique=False)


def downgrade():
    # Remove zola_search_id column
    op.drop_index(op.f('ix_keyword_searches_zola_search_id'), table_name='keyword_searches')
    op.drop_column('keyword_searches', 'zola_search_id')

