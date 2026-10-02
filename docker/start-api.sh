#!/bin/sh
# Startup for the api container (see compose.yaml).
set -e

# Install anything uv.lock has that the image doesn't yet (e.g. after a
# `git pull`). Installs exactly what the lockfile says, without re-resolving,
# so it's instant when nothing changed and works offline.
uv sync --frozen --quiet

# Bring the database schema up to date.
alembic upgrade head

exec uvicorn mtlj.service.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir src
