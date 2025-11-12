"""Add soft delete (deleted_at) to keyword_searches table

Revision ID: add_soft_delete_keyword_searches
Revises: add_zola_search_id
Create Date: 2025-11-11 12:00:00.000000

This migration adds the deleted_at column to support soft delete functionality.
Soft-deleted searches still count toward limit until next month, preventing abuse.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_soft_delete_keyword_searches'
down_revision = 'add_zola_search_id'  # Latest migration: add_zola_search_id
branch_labels = None
depends_on = None


def upgrade():
    """
    Add deleted_at column to keyword_searches table.
    """
    # Add deleted_at column
    op.add_column(
        'keyword_searches',
        sa.Column('deleted_at', sa.DateTime(), nullable=True)
    )
    
    # Create index for soft delete queries
    op.create_index(
        'ix_keyword_searches_deleted_at',
        'keyword_searches',
        ['deleted_at']
    )


def downgrade():
    """
    Remove deleted_at column from keyword_searches table.
    """
    # Drop index
    op.drop_index('ix_keyword_searches_deleted_at', table_name='keyword_searches')
    
    # Drop column
    op.drop_column('keyword_searches', 'deleted_at')

