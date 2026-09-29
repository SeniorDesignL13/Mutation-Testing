"""Database engine, session factory, and the declarative base for ORM models."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from mtlj.service.config import get_settings


class Base(DeclarativeBase):
    """Base class for all ORM models. Alembic autogenerates from ``Base.metadata``."""


engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session."""
    async with SessionLocal() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
"""Type for route parameters that need a DB session: ``session: SessionDep``."""
