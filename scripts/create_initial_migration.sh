#!/bin/bash
# Script to create initial database migration

echo "Creating initial Alembic migration..."

cd "$(dirname "$0")/.."

# Create migration
alembic revision --autogenerate -m "Initial migration with all models"

echo "Migration created! Review the file in migrations/versions/"
echo "Then run: alembic upgrade head"

