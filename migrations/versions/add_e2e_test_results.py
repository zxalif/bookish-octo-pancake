"""add e2e_test_results table

Revision ID: add_e2e_test_results
Revises: add_page_visits
Create Date: 2025-01-XX XX:XX:XX.XXXXXX

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_e2e_test_results'
down_revision = 'add_email_notifications_enabled'  # Points to the current head
branch_labels = None
depends_on = None


def upgrade():
    # Create e2e_test_results table
    op.create_table(
        'e2e_test_results',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('test_run_id', sa.String(length=36), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('triggered_by', sa.String(length=50), nullable=True),
        sa.Column('test_user_email', sa.String(length=255), nullable=True),
        sa.Column('test_user_id', sa.String(length=36), nullable=True),
        sa.Column('duration_ms', sa.Float(), nullable=True),
        sa.Column('steps', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('screenshot_path', sa.String(length=500), nullable=True),
        sa.Column('test_metadata', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes
    op.create_index('ix_e2e_test_results_test_run_id', 'e2e_test_results', ['test_run_id'])
    op.create_index('ix_e2e_test_results_status', 'e2e_test_results', ['status'])
    op.create_index('ix_e2e_test_results_test_user_email', 'e2e_test_results', ['test_user_email'])
    op.create_index('ix_e2e_test_results_test_user_id', 'e2e_test_results', ['test_user_id'])
    op.create_index('ix_e2e_test_results_status_created', 'e2e_test_results', ['status', 'created_at'])


def downgrade():
    # Drop indexes
    op.drop_index('ix_e2e_test_results_status_created', table_name='e2e_test_results')
    op.drop_index('ix_e2e_test_results_test_user_id', table_name='e2e_test_results')
    op.drop_index('ix_e2e_test_results_test_user_email', table_name='e2e_test_results')
    op.drop_index('ix_e2e_test_results_status', table_name='e2e_test_results')
    op.drop_index('ix_e2e_test_results_test_run_id', table_name='e2e_test_results')
    
    # Drop table
    op.drop_table('e2e_test_results')

