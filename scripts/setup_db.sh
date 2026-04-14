#!/usr/bin/env bash
# Run this once on devserver to create the PostgreSQL database + user,
# install Python deps, and run initial migrations.
set -euo pipefail

DB_NAME="mfg_dashboard"
DB_USER="mfg_dashboard"
DB_PASS="mfg_dashboard"  # change if desired

echo "==> Creating PostgreSQL role and database..."
sudo -u postgres psql <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
  END IF;
END
\$\$;

SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')
\gexec
SQL

echo "==> Installing Python dependencies..."
pip install -r requirements.txt

echo "==> Running Flask-Migrate to create tables..."
export DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@localhost/${DB_NAME}"
flask db upgrade

echo ""
echo "Done. Database is ready."
echo "Start the app with:"
echo "  export DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@localhost/${DB_NAME}"
echo "  gunicorn -w 2 -b 0.0.0.0:5000 'run:app'"
