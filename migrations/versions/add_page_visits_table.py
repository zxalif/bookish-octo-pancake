"""Add page_visits table for analytics tracking

Revision ID: add_page_visits
Revises: b2c3d4e5f6a7
Create Date: 2025-11-14 22:50:00.000000

This migration adds the page_visits table for tracking page visits and analytics.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_page_visits'
down_revision = 'b2c3d4e5f6a7'  # Points to add_is_banned_to_users
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create page_visits table for analytics tracking.
    """
    op.create_table(
        'page_visits',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('page_path', sa.String(length=500), nullable=False),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.String(length=500), nullable=True),
        sa.Column('referrer', sa.String(length=1000), nullable=True),
        sa.Column('utm_source', sa.String(length=100), nullable=True),
        sa.Column('utm_medium', sa.String(length=100), nullable=True),
        sa.Column('utm_campaign', sa.String(length=100), nullable=True),
        sa.Column('user_id', sa.String(length=36), nullable=True),
        sa.Column('session_id', sa.String(length=100), nullable=True),
        sa.Column('country', sa.String(length=2), nullable=True),
        sa.Column('device_type', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes
    op.create_index('ix_page_visits_page_path', 'page_visits', ['page_path'])
    op.create_index('ix_page_visits_ip_address', 'page_visits', ['ip_address'])
    op.create_index('ix_page_visits_utm_source', 'page_visits', ['utm_source'])
    op.create_index('ix_page_visits_utm_medium', 'page_visits', ['utm_medium'])
    op.create_index('ix_page_visits_utm_campaign', 'page_visits', ['utm_campaign'])
    op.create_index('ix_page_visits_user_id', 'page_visits', ['user_id'])
    op.create_index('ix_page_visits_session_id', 'page_visits', ['session_id'])
    op.create_index('ix_page_visits_country', 'page_visits', ['country'])
    op.create_index('ix_page_visits_device_type', 'page_visits', ['device_type'])
    op.create_index('ix_page_visits_created_at', 'page_visits', ['created_at'])
    op.create_index('ix_page_visits_page_path_created', 'page_visits', ['page_path', 'created_at'])
    op.create_index('ix_page_visits_user_id_created', 'page_visits', ['user_id', 'created_at'])


def downgrade() -> None:
    """
    Drop page_visits table and indexes.
    """
    # Drop indexes first
    op.drop_index('ix_page_visits_user_id_created', table_name='page_visits')
    op.drop_index('ix_page_visits_page_path_created', table_name='page_visits')
    op.drop_index('ix_page_visits_created_at', table_name='page_visits')
    op.drop_index('ix_page_visits_device_type', table_name='page_visits')
    op.drop_index('ix_page_visits_country', table_name='page_visits')
    op.drop_index('ix_page_visits_session_id', table_name='page_visits')
    op.drop_index('ix_page_visits_user_id', table_name='page_visits')
    op.drop_index('ix_page_visits_utm_campaign', table_name='page_visits')
    op.drop_index('ix_page_visits_utm_medium', table_name='page_visits')
    op.drop_index('ix_page_visits_utm_source', table_name='page_visits')
    op.drop_index('ix_page_visits_ip_address', table_name='page_visits')
    op.drop_index('ix_page_visits_page_path', table_name='page_visits')
    
    # Drop table
    op.drop_table('page_visits')

