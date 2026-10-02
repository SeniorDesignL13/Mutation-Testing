"""Diagnostic routes for verifying the stack is wired up end to end.

Each check returns 200 with ``ok=true`` on success, or 503 with ``ok=false``
and the error in ``detail`` so failures are visible without reading logs.
"""

import time
from collections.abc import Awaitable, Callable

import httpx
from fastapi import APIRouter, Response, status
from sqlalchemy import func, select, text

from mtlj.api.config import get_settings
from mtlj.api.db import SessionDep, engine
from mtlj.api.models import ConnectionCheck
from mtlj.api.schemas.testing import CheckResult, RouteInfo

router = APIRouter(prefix="/testing", tags=["testing"])

ROUTES = [
    RouteInfo(
        name="API health",
        method="GET",
        path="/health",
        description="The API is up and reachable from the browser.",
    ),
    RouteInfo(
        name="Postgres",
        method="GET",
        path="/testing/postgres",
        description="The API can open a connection to the Postgres server.",
    ),
    RouteInfo(
        name="App database",
        method="GET",
        path="/testing/database",
        description="Migrations are applied and the ORM can write and read app tables.",
    ),
    RouteInfo(
        name="External API",
        method="GET",
        path="/testing/external-api",
        description="The API can make outbound HTTPS calls (e.g. to LLM providers).",
    ),
]


async def _run_check(
    name: str, response: Response, check: Callable[[], Awaitable[str]]
) -> CheckResult:
    start = time.perf_counter()
    try:
        detail = await check()
        ok = True
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        ok = False
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    latency_ms = round((time.perf_counter() - start) * 1000, 1)
    return CheckResult(name=name, ok=ok, latency_ms=latency_ms, detail=detail)


@router.get("", response_model=list[RouteInfo])
async def list_routes() -> list[RouteInfo]:
    """List the diagnostic routes, for the frontend's status table."""
    return ROUTES


@router.get("/postgres", response_model=CheckResult)
async def check_postgres(response: Response) -> CheckResult:
    """Open a raw connection and ask Postgres for its version."""

    async def check() -> str:
        async with engine.connect() as conn:
            version = await conn.scalar(text("SHOW server_version"))
        return f"PostgreSQL {version}"

    return await _run_check("Postgres", response, check)


@router.get("/database", response_model=CheckResult)
async def check_database(response: Response, session: SessionDep) -> CheckResult:
    """Write a row to an app table via the ORM and read the table back."""

    async def check() -> str:
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        session.add(ConnectionCheck(source="testing/database"))
        await session.commit()
        count = await session.scalar(select(func.count()).select_from(ConnectionCheck))
        return f"Migration {revision}; {count} rows in connection_checks"

    return await _run_check("App database", response, check)


@router.get("/external-api", response_model=CheckResult)
async def check_external_api(response: Response) -> CheckResult:
    """Make an outbound HTTPS request to a well-known endpoint."""
    url = get_settings().external_check_url

    async def check() -> str:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        return f"{url} -> HTTP {resp.status_code}"

    return await _run_check("External API", response, check)
