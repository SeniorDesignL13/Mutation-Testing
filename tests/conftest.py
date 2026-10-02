"""Shared pytest fixtures."""

import os
import socket
from collections.abc import AsyncIterator

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from mtlj.api.config import get_settings
from mtlj.api.db import engine
from mtlj.api.main import app


def _postgres_is_reachable() -> bool:
    url = make_url(get_settings().database_url)
    try:
        with socket.create_connection((url.host or "localhost", url.port or 5432), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def database() -> None:
    """Make sure Postgres is up and migrated, or skip the test if it isn't running.

    In CI (where ``CI`` is set) a missing database fails instead, so tests are
    never skipped by accident.
    """
    if not _postgres_is_reachable():
        message = "Postgres isn't running. Start it with: docker compose up -d db"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    command.upgrade(Config(toml_file="pyproject.toml"), "head")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client that calls the FastAPI app in-process."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    # Each test runs on its own event loop; pooled asyncpg connections can't cross loops.
    await engine.dispose()
