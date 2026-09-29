"""Response schemas for the ``/testing`` diagnostic routes."""

from pydantic import BaseModel


class RouteInfo(BaseModel):
    """A diagnostic route the frontend can list and call."""

    name: str
    method: str
    path: str
    description: str


class CheckResult(BaseModel):
    """Outcome of a single diagnostic check."""

    name: str
    ok: bool
    latency_ms: float
    detail: str
