from django.db import models
from django.db.backends import ddl_references
from django.db.backends.base import schema as base_schema
from django.db.models.indexes import Index


class UniqueIndex(Index):
    def create_sql(
        self,
        model: type[models.Model],
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        using: str = "",
        **kwargs: object,
    ) -> ddl_references.Statement:
        statement = super().create_sql(model, schema_editor, using, **kwargs)
        statement.template = statement.template.replace(
            "CREATE INDEX", "CREATE UNIQUE INDEX"
        )
        return statement
