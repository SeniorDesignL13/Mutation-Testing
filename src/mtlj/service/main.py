"""FastAPI application entry point (`uvicorn mtlj.service.main:app`)."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mtlj import __version__
from mtlj.service.config import get_settings
from mtlj.service.routers import testing

settings = get_settings()

app = FastAPI(title="mtlj", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.enable_testing_routes:
    app.include_router(testing.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check used by Docker/CI to confirm the service boots."""
    return {"status": "ok"}
