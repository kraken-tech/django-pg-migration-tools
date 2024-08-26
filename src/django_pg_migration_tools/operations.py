from __future__ import annotations

from textwrap import dedent
from typing import Any

from django.contrib.postgres import operations as psql_operations
from django.db import migrations, models, router
from django.db.backends.base import schema as base_schema
from django.db.migrations.operations import base as migrations_base


class BaseIndexOperation(
    migrations_base.Operation, psql_operations.NotInTransactionMixin
):
    reversible = True

    atomic = False

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

    def __init__(
        self,
        model_name: str,
        hints: Any = None,
    ) -> None:
        self.model_name = model_name
        self.original_lock_timeout = ""
        self.hints = {} if hints is None else hints

    def safer_create_index(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
        index: models.Index,
    ) -> None:
        self._ensure_not_in_transaction(schema_editor)

        if not router.allow_migrate(
            schema_editor.connection.alias, app_label, **self.hints
        ):
            return

        self._ensure_no_lock_timeout_set(schema_editor)
        self._ensure_not_an_invalid_index(schema_editor, index)
        model = from_state.apps.get_model(app_label, self.model_name)
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
    ) -> None:
        self._ensure_not_in_transaction(schema_editor)

        if not router.allow_migrate(
            schema_editor.connection.alias, app_label, **self.hints
        ):
            return

        self._ensure_no_lock_timeout_set(schema_editor)
        model = from_state.apps.get_model(app_label, self.model_name)
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


class SaferAddIndexConcurrently(BaseIndexOperation):
    """
    This class mimics the behaviour of:
        django.contrib.postgres.operations.AddIndexConcurrently

    However, it uses `django.db.migrations.operations.base.Operation` as a base
    class due to limitations of Django's AddIndexConcurrently operation.

    One such limitation is that Django's AddIndexConcurrently operation does
    not provide easy hooks so that we can add the conditional `IF NOT EXISTS`
    to the `CREATE INDEX CONCURRENTLY` command, which is something we must have
    here.

    As a compromise, this class implements the same input interface as Django's
    AddIndexConcurrently, so that the developer using it doesn't "feel" any
    differences.
    """

    def __init__(
        self,
        model_name: str,
        index: models.Index,
        hints: Any = None,
    ) -> None:
        self.index = index
        super().__init__(model_name=model_name, hints=hints)

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
        self.safer_create_index(
            app_label, schema_editor, from_state, to_state, self.index
        )

    def database_backwards(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
    ) -> None:
        self.safer_drop_index(
            app_label, schema_editor, from_state, to_state, self.index
        )

    # The following methods are necessary for Django to understand state
    # changes.
    def state_forwards(
        self, app_label: str, state: migrations.state.ProjectState
    ) -> None:
        state.add_index(app_label, self.model_name.lower(), self.index)

    def deconstruct(self) -> tuple[str, list[Any], dict[str, Any]]:
        return (
            self.__class__.__qualname__,
            [],
            {"model_name": self.model_name, "index": self.index},
        )
