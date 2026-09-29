"""SQLAlchemy ORM models.

Import every model module here so ``Base.metadata`` is fully populated when
Alembic autogenerates migrations.
"""

from mtlj.service.db import Base
from mtlj.service.models.connection_check import ConnectionCheck

__all__ = ["Base", "ConnectionCheck"]
