"""Shared pytest fixtures."""

from collections.abc import AsyncIterator

import httpx
import pytest

from mtlj.service.db import engine
from mtlj.service.main import app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client that calls the FastAPI app in-process."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    # Each test runs on its own event loop; pooled asyncpg connections can't cross loops.
    await engine.dispose()
