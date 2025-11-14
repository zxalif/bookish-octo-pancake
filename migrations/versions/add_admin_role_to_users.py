"""add admin role to users

Revision ID: add_admin_role
Revises: a1b2c3d4e5f6
Create Date: 2025-11-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_admin_role'
down_revision = 'a1b2c3d4e5f6'  # Points to current head to merge branches
branch_labels = None
depends_on = None


def upgrade():
    # Add is_admin column to users table
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), nullable=False, server_default='false'))
    op.create_index('ix_users_is_admin', 'users', ['is_admin'])


def downgrade():
    op.drop_index('ix_users_is_admin', 'users')
    op.drop_column('users', 'is_admin')

