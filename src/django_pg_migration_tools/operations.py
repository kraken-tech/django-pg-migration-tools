from __future__ import annotations

from textwrap import dedent

from django.contrib.postgres import operations as psql_operations
from django.db import migrations, models
from django.db.backends.base import schema as base_schema


class BaseIndexOperation:
    SHOW_LOCK_TIMEOUT_QUERY = "SHOW lock_timeout;"

    SET_LOCK_TIMEOUT_QUERY = "SET lock_timeout = %(lock_timeout)s;"

    CHECK_INVALID_INDEX_QUERY = dedent("""
    SELECT relname
    FROM pg_class, pg_index
    WHERE (
        pg_index.indisvalid = false
        AND pg_index.indexrelid = pg_class.oid
        AND relname = %(index_name)s
    );
    """)

    DROP_INDEX_QUERY = 'DROP INDEX CONCURRENTLY IF EXISTS "{}";'

    def safer_create_index(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
        index: models.Index,
        model: type[models.Model],
        operation: SaferAddIndexConcurrently | SaferRemoveIndexConcurrently,
    ) -> None:
        operation._ensure_not_in_transaction(schema_editor)

        if not operation.allow_migrate_model(schema_editor.connection.alias, model):
            return

        self._ensure_no_lock_timeout_set(schema_editor)
        self._ensure_not_an_invalid_index(schema_editor, index)
        index_sql = str(index.create_sql(model, schema_editor, concurrently=True))
        # Inject the IF NOT EXISTS because Django doesn't provide a handy
        # if_not_exists: bool parameter for us to use.
        index_sql = index_sql.replace(
            "CREATE INDEX CONCURRENTLY", "CREATE INDEX CONCURRENTLY IF NOT EXISTS"
        )
        schema_editor.execute(index_sql)
        self._ensure_original_lock_timeout_is_reset(schema_editor)

    def safer_drop_index(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
        index: models.Index,
        model: type[models.Model],
        operation: SaferAddIndexConcurrently | SaferRemoveIndexConcurrently,
    ) -> None:
        operation._ensure_not_in_transaction(schema_editor)

        if not operation.allow_migrate_model(schema_editor.connection.alias, model):
            return

        self._ensure_no_lock_timeout_set(schema_editor)
        index_sql = str(index.remove_sql(model, schema_editor, concurrently=True))
        # Differently from the CREATE INDEX operation, Django already provides
        # us with IF EXISTS when dropping an index... We don't have to do that
        # .replace() call here.
        schema_editor.execute(index_sql)
        self._ensure_original_lock_timeout_is_reset(schema_editor)

    def _ensure_no_lock_timeout_set(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
    ) -> None:
        cursor = schema_editor.connection.cursor()
        cursor.execute(self.SHOW_LOCK_TIMEOUT_QUERY)
        self.original_lock_timeout = cursor.fetchone()[0]
        cursor.execute(self.SET_LOCK_TIMEOUT_QUERY, {"lock_timeout": 0})

    def _ensure_not_an_invalid_index(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        index: models.Index,
    ) -> None:
        """
        It is possible that the migration would have failed when:

          1. We created an index manually and it failed and we didn't notice.
          2. The migration is being automatically retried and the first
             attempt failed and generated an invalid index.

        One potential cause of failure that might trigger number 2 is if we
        have deadlocks on the table at the time the migration runs. Another is
        if the migrations was accidentally ran with a lock_timeout value, and
        the operation timed out.

        In those cases we want to drop the invalid index first so that it can
        be recreated on next steps via CREATE INDEX CONCURRENTLY IF EXISTS.
        """
        cursor = schema_editor.connection.cursor()
        cursor.execute(self.CHECK_INVALID_INDEX_QUERY, {"index_name": index.name})
        if cursor.fetchone():
            cursor.execute(self.DROP_INDEX_QUERY.format(index.name))

    def _ensure_original_lock_timeout_is_reset(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
    ) -> None:
        cursor = schema_editor.connection.cursor()
        cursor.execute(
            self.SET_LOCK_TIMEOUT_QUERY, {"lock_timeout": self.original_lock_timeout}
        )


class SaferAddIndexConcurrently(
    BaseIndexOperation, psql_operations.AddIndexConcurrently
):
    """
    This class inherits the behaviour of:
        django.contrib.postgres.operations.AddIndexConcurrently

    However, it overrides the relevant database_forwards and database_backwards
    operations to take into consideration lock timeouts, invalid indexes, and
    idempotency.
    """

    model_name: str
    index: models.Index

    def describe(self) -> str:
        return (
            f"Concurrently creates index {self.index.name} on field(s) "
            f"{self.index.fields} of model {self.model_name} if the index "
            f"does not exist. NOTE: Using django_pg_migration_tools "
            f"SaferAddIndexConcurrently operation."
        )

    def database_forwards(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
    ) -> None:
        model = to_state.apps.get_model(app_label, self.model_name)
        self.safer_create_index(
            app_label=app_label,
            schema_editor=schema_editor,
            from_state=from_state,
            to_state=to_state,
            index=self.index,
            model=model,
            operation=self,
        )

    def database_backwards(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
    ) -> None:
        model = from_state.apps.get_model(app_label, self.model_name)
        self.safer_drop_index(
            app_label=app_label,
            schema_editor=schema_editor,
            from_state=from_state,
            to_state=to_state,
            index=self.index,
            model=model,
            operation=self,
        )


class SaferRemoveIndexConcurrently(
    BaseIndexOperation, psql_operations.RemoveIndexConcurrently
):
    model_name: str
    name: str

    def describe(self) -> str:
        return (
            f"Concurrently removes index {self.name} on model {self.model_name} "
            "if the index exists. NOTE: Using django_pg_migration_tools "
            f"SaferRemoveIndexConcurrently operation."
        )

    def database_forwards(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
    ) -> None:
        model = from_state.apps.get_model(app_label, self.model_name)
        from_model_state = from_state.models[app_label, self.model_name.lower()]
        index = from_model_state.get_index_by_name(self.name)
        self.safer_drop_index(
            app_label=app_label,
            schema_editor=schema_editor,
            from_state=from_state,
            to_state=to_state,
            index=index,
            model=model,
            operation=self,
        )

    def database_backwards(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
    ) -> None:
        model = to_state.apps.get_model(app_label, self.model_name)
        to_model_state = to_state.models[app_label, self.model_name.lower()]
        index = to_model_state.get_index_by_name(self.name)
        self.safer_create_index(
            app_label=app_label,
            schema_editor=schema_editor,
            from_state=from_state,
            to_state=to_state,
            index=index,
            model=model,
            operation=self,
        )
