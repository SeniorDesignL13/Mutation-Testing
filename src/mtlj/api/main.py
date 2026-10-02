"""Creates the FastAPI app (`uvicorn mtlj.api.main:app`) and registers its routers."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mtlj import __version__
from mtlj.api.config import get_settings
from mtlj.api.routers import testing

settings = get_settings()

app = FastAPI(title="mtlj", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.enable_testing_routes:
    app.include_router(testing.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check used by Docker/CI to confirm the API boots."""
    return {"status": "ok"}
