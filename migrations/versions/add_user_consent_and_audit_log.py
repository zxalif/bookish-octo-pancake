"""Add user consent tracking and audit log

Revision ID: add_user_consent_audit
Revises: add_support_threads
Create Date: 2024-12-20

Adds GDPR/CCPA consent tracking fields to users table and creates user_audit_logs table
for tracking user changes with IP addresses.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_user_consent_audit'
down_revision = 'add_support_threads'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add consent tracking fields to users table
    op.add_column('users', sa.Column('consent_data_processing', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('users', sa.Column('consent_marketing', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('users', sa.Column('consent_cookies', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('users', sa.Column('consent_data_processing_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('consent_marketing_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('consent_cookies_at', sa.DateTime(), nullable=True))
    
    # Add IP address tracking fields to users table
    op.add_column('users', sa.Column('registration_ip', sa.String(length=45), nullable=True))
    op.add_column('users', sa.Column('last_login_ip', sa.String(length=45), nullable=True))
    
    # Create user_audit_logs table
    op.create_table(
        'user_audit_logs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.String(length=500), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for user_audit_logs
    op.create_index('ix_user_audit_logs_user_id', 'user_audit_logs', ['user_id'], unique=False)
    op.create_index('ix_user_audit_logs_action', 'user_audit_logs', ['action'], unique=False)
    op.create_index('ix_user_audit_logs_user_action', 'user_audit_logs', ['user_id', 'action'], unique=False)
    op.create_index('ix_user_audit_logs_created_at', 'user_audit_logs', ['created_at'], unique=False)


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_user_audit_logs_created_at', table_name='user_audit_logs')
    op.drop_index('ix_user_audit_logs_user_action', table_name='user_audit_logs')
    op.drop_index('ix_user_audit_logs_action', table_name='user_audit_logs')
    op.drop_index('ix_user_audit_logs_user_id', table_name='user_audit_logs')
    
    # Drop user_audit_logs table
    op.drop_table('user_audit_logs')
    
    # Remove IP address tracking fields
    op.drop_column('users', 'last_login_ip')
    op.drop_column('users', 'registration_ip')
    
    # Remove consent tracking fields
    op.drop_column('users', 'consent_cookies_at')
    op.drop_column('users', 'consent_marketing_at')
    op.drop_column('users', 'consent_data_processing_at')
    op.drop_column('users', 'consent_cookies')
    op.drop_column('users', 'consent_marketing')
    op.drop_column('users', 'consent_data_processing')

