from typing import Any

from django.db.backends import ddl_references
from django.db.models.indexes import Index


class UniqueIndex(Index):
    def create_sql(self, *args: Any, **kwargs: Any) -> ddl_references.Statement:
        statement = super().create_sql(*args, **kwargs)
        statement.template = statement.template.replace(
            "CREATE INDEX", "CREATE UNIQUE INDEX"
        )
        return statement
