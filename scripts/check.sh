#!/bin/sh
# Run the Python checks CI runs, auto-fixing lint and formatting first.
# Usage (with the stack running):  docker compose exec api sh scripts/check.sh
set -e

echo "==> Lint + format (auto-fix)"
ruff check --fix .
ruff format .

echo "==> Models match migrations"
alembic check

echo "==> Tests"
pytest -q
