"""Add billing fields to subscription

Revision ID: a1b2c3d4e5f6
Revises: add_scheduling_keyword_searches
Create Date: 2025-01-13 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'add_scheduling_keyword_searches'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new billing fields to subscriptions table
    op.add_column('subscriptions', sa.Column('last_billing_date', sa.DateTime(), nullable=True))
    op.add_column('subscriptions', sa.Column('next_billing_date', sa.DateTime(), nullable=True))
    op.add_column('subscriptions', sa.Column('last_billing_status', sa.String(length=50), nullable=True))
    op.add_column('subscriptions', sa.Column('trial_end_date', sa.DateTime(), nullable=True))
    
    # Create indexes for the new date fields for better query performance
    op.create_index('ix_subscriptions_last_billing_date', 'subscriptions', ['last_billing_date'], unique=False)
    op.create_index('ix_subscriptions_next_billing_date', 'subscriptions', ['next_billing_date'], unique=False)


def downgrade() -> None:
    # Drop indexes first
    op.drop_index('ix_subscriptions_next_billing_date', table_name='subscriptions')
    op.drop_index('ix_subscriptions_last_billing_date', table_name='subscriptions')
    
    # Drop columns
    op.drop_column('subscriptions', 'trial_end_date')
    op.drop_column('subscriptions', 'last_billing_status')
    op.drop_column('subscriptions', 'next_billing_date')
    op.drop_column('subscriptions', 'last_billing_date')

