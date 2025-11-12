"""Add support_threads and support_messages tables

Revision ID: add_support_threads
Revises: add_soft_delete_keyword_searches
Create Date: 2025-11-11 15:00:00.000000

This migration adds support thread and message tables for customer support functionality.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_support_threads'
down_revision = 'add_soft_delete_keyword_searches'
branch_labels = None
depends_on = None


def upgrade():
    """
    Create support_threads and support_messages tables.
    """
    # Create support_threads table
    op.create_table(
        'support_threads',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('subject', sa.String(), nullable=False),
        sa.Column('status', sa.Enum('OPEN', 'PENDING', 'CLOSED', name='threadstatus'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for support_threads
    op.create_index('ix_support_threads_user_id', 'support_threads', ['user_id'])
    op.create_index('ix_support_threads_status', 'support_threads', ['status'])
    
    # Create support_messages table
    op.create_table(
        'support_messages',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('thread_id', sa.String(), nullable=False),
        sa.Column('content', sa.String(), nullable=False),
        sa.Column('sender', sa.Enum('USER', 'SUPPORT', name='messagesender'), nullable=False),
        sa.Column('read', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['thread_id'], ['support_threads.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for support_messages
    op.create_index('ix_support_messages_thread_id', 'support_messages', ['thread_id'])
    op.create_index('ix_support_messages_sender', 'support_messages', ['sender'])
    op.create_index('ix_support_messages_read', 'support_messages', ['read'])


def downgrade():
    """
    Drop support_threads and support_messages tables.
    """
    # Drop indexes
    op.drop_index('ix_support_messages_read', table_name='support_messages')
    op.drop_index('ix_support_messages_sender', table_name='support_messages')
    op.drop_index('ix_support_messages_thread_id', table_name='support_messages')
    op.drop_index('ix_support_threads_status', table_name='support_threads')
    op.drop_index('ix_support_threads_user_id', table_name='support_threads')
    
    # Drop tables
    op.drop_table('support_messages')
    op.drop_table('support_threads')
    
    # Drop enums
    op.execute('DROP TYPE IF EXISTS messagesender')
    op.execute('DROP TYPE IF EXISTS threadstatus')

