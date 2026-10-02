"""Tests for the ``/testing`` diagnostic routes.

The Postgres and database checks use the ``database`` fixture, so they skip
when Postgres isn't running (start it with ``docker compose up -d db``).
"""

import httpx
import pytest

from mtlj.api.config import get_settings


async def test_list_routes_includes_every_check(client: httpx.AsyncClient) -> None:
    response = await client.get("/testing")

    assert response.status_code == 200
    paths = {route["path"] for route in response.json()}
    assert paths == {"/health", "/testing/postgres", "/testing/database", "/testing/external-api"}


@pytest.mark.usefixtures("database")
async def test_postgres_check_succeeds(client: httpx.AsyncClient) -> None:
    response = await client.get("/testing/postgres")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["detail"].startswith("PostgreSQL")


@pytest.mark.usefixtures("database")
async def test_database_check_writes_and_reads_a_row(client: httpx.AsyncClient) -> None:
    first = (await client.get("/testing/database")).json()
    second = (await client.get("/testing/database")).json()

    assert first["ok"] is True
    assert second["ok"] is True
    assert "rows in connection_checks" in second["detail"]


async def test_external_api_check_reports_failure_as_503(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Port 9 (discard) on localhost refuses connections, so this never leaves the machine.
    monkeypatch.setattr(get_settings(), "external_check_url", "http://127.0.0.1:9")

    response = await client.get("/testing/external-api")

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert "ConnectError" in body["detail"]
