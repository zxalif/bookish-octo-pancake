"""Add is_banned field to users

Revision ID: b2c3d4e5f6a7
Revises: add_admin_role
Create Date: 2025-11-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'add_admin_role'  # Points to add_admin_role to maintain chain
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add is_banned column to users table
    op.add_column('users', sa.Column('is_banned', sa.Boolean(), nullable=False, server_default='false'))
    op.create_index('ix_users_is_banned', 'users', ['is_banned'])


def downgrade() -> None:
    # Drop index first
    op.drop_index('ix_users_is_banned', table_name='users')
    # Drop column
    op.drop_column('users', 'is_banned')

