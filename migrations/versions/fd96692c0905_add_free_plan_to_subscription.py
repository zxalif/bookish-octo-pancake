"""Add FREE plan to SubscriptionPlan enum

Revision ID: fd96692c0905
Revises: 11545e36d759
Create Date: 2025-11-09

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fd96692c0905'
down_revision = '11545e36d759'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'FREE' value to subscriptionplan enum
    # PostgreSQL allows adding enum values, but we need to check if it exists first
    op.execute("""
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_enum 
                WHERE enumlabel = 'FREE' 
                AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'subscriptionplan')
            ) THEN
                ALTER TYPE subscriptionplan ADD VALUE 'FREE';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # Note: PostgreSQL doesn't support removing enum values directly
    # This would require recreating the enum type, which is complex
    # For now, we'll leave it as a no-op
    # In production, you'd need to:
    # 1. Create new enum without FREE
    # 2. Update all subscriptions using FREE
    # 3. Drop old enum
    # 4. Rename new enum
    pass

