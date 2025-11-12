#!/bin/bash
# Run database migrations in Docker container

set -e

echo "Running database migrations..."

# Run migrations
alembic upgrade head

echo "Migrations completed successfully!"

