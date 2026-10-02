#!/bin/sh
# Startup for the api container (see compose.yaml).
set -e

# Install the project, plus anything uv.lock gained since the image was built.
# Instant when nothing changed.
uv sync --frozen --quiet

# Bring the database schema up to date.
alembic upgrade head

exec uvicorn mtlj.service.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir src
