"""SQLAlchemy ORM models.

Import every model module here so ``Base.metadata`` is fully populated when
Alembic autogenerates migrations.
"""

from mtlj.api.db import Base
from mtlj.api.models.connection_check import ConnectionCheck

__all__ = ["Base", "ConnectionCheck"]
