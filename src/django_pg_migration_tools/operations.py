from __future__ import annotations

from textwrap import dedent

from django.contrib.postgres import operations as psql_operations
from django.db import migrations, models
from django.db.backends.base import schema as base_schema
from django.db.migrations.operations import base as base_operations
from django.db.migrations.operations import models as operation_models


class TimeoutQueries:
    SHOW_LOCK_TIMEOUT = "SHOW lock_timeout;"
    SET_LOCK_TIMEOUT = "SET lock_timeout = %(lock_timeout)s;"


class IndexQueries:
    CHECK_INVALID_INDEX = dedent("""
    SELECT relname
    FROM pg_class, pg_index
    WHERE (
        pg_index.indisvalid = false
        AND pg_index.indexrelid = pg_class.oid
        AND relname = %(index_name)s
    );
    """)
    DROP_INDEX = 'DROP INDEX CONCURRENTLY IF EXISTS "{}";'


class ConstraintQueries:
    CHECK_EXISTING_CONSTRAINT = dedent("""
        SELECT conname
        FROM pg_catalog.pg_constraint
        WHERE conname = %(constraint_name)s;
    """)


class SafeIndexOperationManager(
    psql_operations.NotInTransactionMixin,
    base_operations.Operation,
):
    def safer_create_index(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
        index: models.Index,
        unique: bool,
        model: type[models.Model],
    ) -> None:
        self._ensure_not_in_transaction(schema_editor)

        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return

        original_lock_timeout = self._show_lock_timeout(schema_editor)
        self._set_lock_timeout(schema_editor, "0")

        self._ensure_not_an_invalid_index(schema_editor, index)
        index_sql = str(index.create_sql(model, schema_editor, concurrently=True))
        # Inject the IF NOT EXISTS because Django doesn't provide a handy
        # if_not_exists: bool parameter for us to use.
        index_sql = index_sql.replace(
            "CREATE INDEX CONCURRENTLY", "CREATE INDEX CONCURRENTLY IF NOT EXISTS"
        )
        if unique:
            index_sql = index_sql.replace("CREATE INDEX", "CREATE UNIQUE INDEX")

        schema_editor.execute(index_sql)

        self._set_lock_timeout(schema_editor, original_lock_timeout)

    def safer_drop_index(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
        index: models.Index,
        model: type[models.Model],
    ) -> None:
        self._ensure_not_in_transaction(schema_editor)

        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return

        original_lock_timeout = self._show_lock_timeout(schema_editor)
        self._set_lock_timeout(schema_editor, "0")

        index_sql = str(index.remove_sql(model, schema_editor, concurrently=True))
        # Differently from the CREATE INDEX operation, Django already provides
        # us with IF EXISTS when dropping an index... We don't have to do that
        # .replace() call here.
        schema_editor.execute(index_sql)

        self._set_lock_timeout(schema_editor, original_lock_timeout)

    def _set_lock_timeout(
        self, schema_editor: base_schema.BaseDatabaseSchemaEditor, value: str
    ) -> None:
        cursor = schema_editor.connection.cursor()
        cursor.execute(TimeoutQueries.SET_LOCK_TIMEOUT, {"lock_timeout": value})

    def _show_lock_timeout(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
    ) -> str:
        cursor = schema_editor.connection.cursor()
        cursor.execute(TimeoutQueries.SHOW_LOCK_TIMEOUT)
        result = cursor.fetchone()[0]
        assert isinstance(result, str)
        return result

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
        cursor.execute(IndexQueries.CHECK_INVALID_INDEX, {"index_name": index.name})
        if cursor.fetchone():
            cursor.execute(IndexQueries.DROP_INDEX.format(index.name))


class ConstraintOperationError(Exception):
    pass


class ConstraintAlreadyExists(ConstraintOperationError):
    pass


class SafeConstraintOperationManager(base_operations.Operation):
    def create_constraint(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
        raise_if_exists: bool,
        model: type[models.Model],
        constraint: models.UniqueConstraint,
    ) -> None:
        psql_operations.NotInTransactionMixin()._ensure_not_in_transaction(
            schema_editor
        )

        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return

        index = self._get_index_for_constraint(constraint)

        if constraint.condition is not None:
            """
            Unique constraints with conditions do not exist in postgres.

            As of writing Django handles these as unique indexes with conditions only
            in the auto generated operation, so we only create the index and finish here
            """
            SafeIndexOperationManager().safer_create_index(
                app_label=app_label,
                schema_editor=schema_editor,
                from_state=from_state,
                to_state=to_state,
                index=index,
                model=model,
                unique=True,
            )
            return

        if not self._can_create_constraint(schema_editor, constraint, raise_if_exists):
            return

        SafeIndexOperationManager().safer_create_index(
            app_label=app_label,
            schema_editor=schema_editor,
            from_state=from_state,
            to_state=to_state,
            index=index,
            model=model,
            unique=True,
        )

        # Django doesn't have a handy flag "using=..." so we need to alter the
        # SQL statement manually. We go from a SQL that looks like this:
        #
        # - ALTER TABLE "table" ADD CONSTRAINT "constraint" UNIQUE ("field")
        #
        # Into a SQL that looks like:
        #
        # - ALTER TABLE "table" ADD CONSTRAINT "constraint" UNIQUE USING INDEX "idx"
        base_sql = str(constraint.create_sql(model, schema_editor))
        alter_table_sql = base_sql.split(" UNIQUE")[0]
        sql = f'{alter_table_sql} UNIQUE USING INDEX "{index.name}"'

        # Now we can execute the schema change. We have lock timeouts back in
        # place after creating the index that would prevent this operation from
        # running for too long if it's blocked by another query. Otherwise,
        # this operation should actually be quite fast - if it's not blocked -
        # since we have created the unique index in the previous step.
        return schema_editor.execute(sql)

    def drop_constraint(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
        model: type[models.Model],
        constraint: models.UniqueConstraint,
    ) -> None:
        psql_operations.NotInTransactionMixin()._ensure_not_in_transaction(
            schema_editor
        )

        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return

        if constraint.condition is not None:
            # If condition is present on the constraint, it would have been created
            # as an index instead, so index is instead removed
            index = self._get_index_for_constraint(constraint)

            SafeIndexOperationManager().safer_drop_index(
                app_label=app_label,
                schema_editor=schema_editor,
                from_state=from_state,
                to_state=to_state,
                index=index,
                model=model,
            )
            return

        if not self._constraint_exists(schema_editor, constraint):
            # Nothing to delete.
            return

        schema_editor.remove_constraint(model, constraint)

    def _can_create_constraint(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        constraint: models.UniqueConstraint,
        raise_if_exists: bool,
    ) -> bool:
        constraint_exists = self._constraint_exists(schema_editor, constraint)
        if raise_if_exists and constraint_exists:
            raise ConstraintAlreadyExists(
                f"Cannot create a constraint with the name "
                f"{constraint.name} because a constraint of the same "
                f"name already exists. If you want to skip this operation "
                f"when the constraint already exists, run the operation "
                f"with the flag `skip_if_exists=True`."
            )
        # We can't re-create a constraint that already exists because the
        # ALTER TABLE ... ADD CONSTRAINT is not idempotent.
        return not constraint_exists

    def _get_index_for_constraint(
        self, constraint: models.UniqueConstraint
    ) -> models.Index:
        return models.Index(
            *constraint.expressions,
            fields=constraint.fields,
            name=constraint.name,
            condition=constraint.condition,
            opclasses=constraint.opclasses,  # type: ignore[attr-defined]
        )

    def _constraint_exists(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        constraint: models.UniqueConstraint,
    ) -> bool:
        cursor = schema_editor.connection.cursor()
        cursor.execute(
            ConstraintQueries.CHECK_EXISTING_CONSTRAINT,
            {"constraint_name": constraint.name},
        )
        return bool(cursor.fetchone())


class SaferAddIndexConcurrently(psql_operations.AddIndexConcurrently):
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
        SafeIndexOperationManager().safer_create_index(
            app_label=app_label,
            schema_editor=schema_editor,
            from_state=from_state,
            to_state=to_state,
            index=self.index,
            model=model,
            unique=False,
        )

    def database_backwards(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
    ) -> None:
        model = from_state.apps.get_model(app_label, self.model_name)
        SafeIndexOperationManager().safer_drop_index(
            app_label=app_label,
            schema_editor=schema_editor,
            from_state=from_state,
            to_state=to_state,
            index=self.index,
            model=model,
        )


class SaferRemoveIndexConcurrently(psql_operations.RemoveIndexConcurrently):
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
        SafeIndexOperationManager().safer_drop_index(
            app_label=app_label,
            schema_editor=schema_editor,
            from_state=from_state,
            to_state=to_state,
            index=index,
            model=model,
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
        SafeIndexOperationManager().safer_create_index(
            app_label=app_label,
            schema_editor=schema_editor,
            from_state=from_state,
            to_state=to_state,
            index=index,
            model=model,
            unique=False,
        )


class SaferAddUniqueConstraint(operation_models.AddConstraint):
    model_name: str
    constraint: models.UniqueConstraint
    raise_if_exists: bool

    def __init__(
        self,
        model_name: str,
        constraint: models.UniqueConstraint,
        raise_if_exists: bool = True,
    ) -> None:
        self.raise_if_exists = raise_if_exists
        self.constraint = constraint

        # Perform a basic input-validation at initialisation time rather than
        # at migration time ("database_forwards"/"database_backwards") so that
        # all the errors we __can__ raise at initialisation time are raised
        # when the programmer is still coding rather than when the migration
        # effectively runs. This is the "Django way" of doing it, as other
        # operation classes take this same approach. See "AddIndex" operation.
        self._validate()

        super().__init__(model_name, constraint)

    def database_forwards(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
    ) -> None:
        SafeConstraintOperationManager().create_constraint(
            app_label=app_label,
            schema_editor=schema_editor,
            from_state=from_state,
            to_state=to_state,
            model=to_state.apps.get_model(app_label, self.model_name),
            raise_if_exists=self.raise_if_exists,
            constraint=self.constraint,
        )

    def database_backwards(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
    ) -> None:
        SafeConstraintOperationManager().drop_constraint(
            app_label=app_label,
            schema_editor=schema_editor,
            from_state=from_state,
            to_state=to_state,
            model=to_state.apps.get_model(app_label, self.model_name),
            constraint=self.constraint,
        )

    def _validate(self) -> None:
        if not isinstance(self.constraint, models.UniqueConstraint):
            raise ValueError(
                "SaferAddUniqueConstraint only supports the UniqueConstraint class"
            )

    def describe(self) -> str:
        return (
            f"Concurrently adds a UNIQUE index {self.constraint.name} on model "
            f"{self.model_name} on field(s) {self.constraint.fields} if the "
            f"index does not exist. Then, adds the constraint using the just-created "
            f"index. NOTE: Using django_pg_migration_tools SaferAddUniqueConstraint "
            f"operation."
        )


class SaferRemoveUniqueConstraint(operation_models.RemoveConstraint):
    model_name: str
    name: str

    def database_forwards(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
    ) -> None:
        model = from_state.apps.get_model(app_label, self.model_name)
        from_model_state = from_state.models[app_label, self.model_name.lower()]
        SafeConstraintOperationManager().drop_constraint(
            app_label=app_label,
            schema_editor=schema_editor,
            from_state=from_state,
            to_state=to_state,
            model=model,
            constraint=from_model_state.get_constraint_by_name(self.name),
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
        SafeConstraintOperationManager().create_constraint(
            app_label=app_label,
            schema_editor=schema_editor,
            from_state=from_state,
            to_state=to_state,
            model=model,
            raise_if_exists=False,
            constraint=to_model_state.get_constraint_by_name(self.name),
        )

    def describe(self) -> str:
        return (
            f"Checks if the constraint {self.name} exists, and if so, removes "
            f"it. If the migration is reversed, it will recreate the constraint "
            f"using a UNIQUE index. NOTE: Using the django_pg_migration_tools "
            f"SaferRemoveIndexConcurrently operation."
        )
