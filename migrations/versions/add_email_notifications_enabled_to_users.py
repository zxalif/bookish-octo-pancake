"""add_email_notifications_enabled_to_users

Revision ID: add_email_notifications_enabled
Revises: 
Create Date: 2025-01-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_email_notifications_enabled'
down_revision = 'add_page_visits'  # Current head migration
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add email_notifications_enabled column to users table.
    
    This allows users to opt out of email notifications for new leads.
    Defaults to True (enabled) for existing users.
    """
    op.add_column(
        'users',
        sa.Column(
            'email_notifications_enabled',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true')
        )
    )


def downgrade() -> None:
    """
    Remove email_notifications_enabled column from users table.
    """
    op.drop_column('users', 'email_notifications_enabled')

