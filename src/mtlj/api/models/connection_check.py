"""Rows written by the ``/testing/database`` diagnostic route."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from mtlj.api.db import Base


class ConnectionCheck(Base):
    """One successful round-trip through the ORM and the app's own schema."""

    __tablename__ = "connection_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
