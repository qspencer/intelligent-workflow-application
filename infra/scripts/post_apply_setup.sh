#!/usr/bin/env bash
# One-time setup that has to happen *after* `terraform apply`:
# 1. Enable the pgvector extension on the RDS database.
# 2. Apply Alembic migrations against RDS.
#
# Requires: psql, the backend dev environment (uv + pyproject.toml).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$INFRA_DIR")"

cd "$INFRA_DIR"

DB_URL="$(aws secretsmanager get-secret-value \
  --secret-id "$(terraform output -raw -no-color rds_endpoint | cut -d: -f1)/database_url" \
  --query SecretString --output text 2>/dev/null \
  || aws secretsmanager get-secret-value \
       --secret-id "workflow-platform/dev/database_url" \
       --query SecretString --output text)"

# Convert SQLAlchemy URL to libpq URL (drop the +asyncpg).
PSQL_URL="$(echo "$DB_URL" | sed 's|postgresql+asyncpg://|postgresql://|; s|?ssl=require|?sslmode=require|')"

echo "Enabling pgvector extension"
psql "$PSQL_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;"

echo "Applying Alembic migrations"
cd "$REPO_ROOT/backend"
DATABASE_URL="$DB_URL" uv run alembic upgrade head

echo "Done. The backend can now talk to a fully-migrated database."
