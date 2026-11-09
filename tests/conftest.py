"""Shared pytest fixtures."""

import ipaddress
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


def in_ci() -> bool:
    """Whether ``CI`` is set to a true value, as GitHub Actions sets it (``CI=true``)."""
    return os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """In CI, refuse to run tests that call a real judge.

    CI runs entirely on recorded scores (MTLJ-90, docs/fixtures.md): no API keys, no inference
    cost. ``integration`` tests are deselected by default; this stops them being selected in CI
    by accident (e.g. ``-m integration``). An unmarked test that reaches out anyway is stopped by
    ``_no_outside_network_in_ci``.

    ``trylast``, so it sees the tests left after ``-m`` has deselected the rest. Run earlier, it
    would see every collected test, deselected ones included, and fail every CI run.
    """
    if not in_ci():
        return
    live = [item.nodeid for item in items if item.get_closest_marker("integration")]
    if live:
        raise pytest.UsageError(
            "CI runs on recorded judge scores only, but these tests call a real judge: "
            + ", ".join(live[:5])
            + ". Replay from fixtures instead (see docs/fixtures.md)."
        )


def _allowed_host(host: object) -> bool:
    """Loopback, and the test database's host: the only machines a test may reach."""
    if host in ("localhost", make_url(get_settings().database_url).host):
        return True
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False  # any other host name


def block_outside_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any connection to, or name lookup of, another machine fail the test.

    Loopback stays allowed: on Windows, asyncio's event loop wakes itself through a socket pair
    that connects to 127.0.0.1, which is no network call.
    """
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def guard(real):
        def connect(sock, address, *args, **kwargs):
            host = address[0] if isinstance(address, tuple) else address
            if not _allowed_host(host):
                raise AssertionError(f"a network connection was attempted: {address!r}")
            return real(sock, address, *args, **kwargs)

        return connect

    def getaddrinfo(host, *args, **kwargs):
        if host not in (None, "") and not _allowed_host(host):
            raise AssertionError(f"a network connection was attempted: lookup of {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guard(real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(real_connect_ex))
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if it reaches another machine, wherever it runs."""
    block_outside_network(monkeypatch)


@pytest.fixture(autouse=True)
def _no_outside_network_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """In CI, every test runs with the outside network blocked (MTLJ-90).

    So "CI runs entirely in replay mode" doesn't rest on remembering the ``integration``
    marker: a test that would call a real judge fails here instead of spending money.
    """
    if in_ci():
        block_outside_network(monkeypatch)


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
