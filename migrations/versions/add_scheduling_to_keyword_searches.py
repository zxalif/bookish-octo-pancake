"""Add scheduling fields to keyword searches

Revision ID: add_scheduling_keyword_searches
Revises: add_user_consent_audit
Create Date: 2024-12-20

Adds scraping_mode and scraping_interval fields to keyword_searches table
to support scheduled scraping in addition to one-time scraping.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_scheduling_keyword_searches'
down_revision = 'add_user_consent_audit'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add scraping_mode column (default: "one_time" for backward compatibility)
    op.add_column('keyword_searches', sa.Column('scraping_mode', sa.String(length=20), nullable=False, server_default='one_time'))
    
    # Add scraping_interval column (nullable, only used for scheduled mode)
    op.add_column('keyword_searches', sa.Column('scraping_interval', sa.String(length=10), nullable=True))


def downgrade() -> None:
    # Drop columns
    op.drop_column('keyword_searches', 'scraping_interval')
    op.drop_column('keyword_searches', 'scraping_mode')

