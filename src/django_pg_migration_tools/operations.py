from __future__ import annotations

from textwrap import dedent

from django.contrib.postgres import operations as psql_operations
from django.db import migrations, models
from django.db.backends import utils as django_backends_utils
from django.db.backends.base import schema as base_schema
from django.db.migrations.operations import base as base_operations
from django.db.migrations.operations import fields as operation_fields
from django.db.migrations.operations import models as operation_models


try:
    from psycopg import sql as psycopg_sql
except ImportError:  # pragma: no cover
    try:
        from psycopg2 import sql as psycopg_sql  # type: ignore[no-redef]
    except ImportError:
        raise ImportError("Neither psycopg2 nor psycopg (3) is installed.")


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

    CHECK_CONSTRAINT_IS_VALID = dedent("""
        SELECT 1
        FROM pg_catalog.pg_constraint
        WHERE
            conname = %(constraint_name)s
            AND convalidated IS TRUE;
    """)

    CHECK_CONSTRAINT_IS_NOT_VALID = dedent("""
        SELECT 1
        FROM pg_catalog.pg_constraint
        WHERE
            conname = %(constraint_name)s
            AND convalidated IS FALSE;
    """)

    ALTER_TABLE_CONSTRAINT_NOT_NULL_NOT_VALID = dedent("""
        ALTER TABLE {table_name}
        ADD CONSTRAINT {constraint_name}
        CHECK ({column_name} IS NOT NULL) NOT VALID;
    """)

    ALTER_TABLE_DROP_CONSTRAINT = dedent("""
        ALTER TABLE {table_name}
        DROP CONSTRAINT {constraint_name};
    """)

    ALTER_TABLE_VALIDATE_CONSTRAINT = dedent("""
        ALTER TABLE {table_name}
        VALIDATE CONSTRAINT {constraint_name};
    """)


class NullabilityQueries:
    IS_COLUMN_NOT_NULL = dedent("""
        SELECT 1
        FROM pg_catalog.pg_attribute
        WHERE
            attrelid = %(table_name)s::regclass
            AND attname = %(column_name)s
            AND attnotnull IS TRUE;
    """)

    ALTER_TABLE_SET_NOT_NULL = dedent("""
        ALTER TABLE {table_name}
        ALTER COLUMN {column_name}
        SET NOT NULL;
    """)

    ALTER_TABLE_DROP_NOT_NULL = dedent("""
        ALTER TABLE {table_name}
        ALTER COLUMN {column_name}
        DROP NOT NULL;
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


class NullsManager(base_operations.Operation):
    def set_not_null(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        model: type[models.Model],
        column_name: str,
    ) -> None:
        """
        Set column_name to NOT NULL without blocking reads/writes for too long.

        On a high-level, this routine will:

        1. Add a NOT NULL check constraint that's initially NOT VALID.
        2. Validate that constraint.
        3. Set NOT NULL on the column. Note: Postgres will internally use the
           constraint above instead of performing a table scan.
        4. Drop the constraint.

        The following routine takes into consideration:

        Reentrancy:
          Any of the given operations below may fail for a variety of
          reasons. If a failure occurs, this routine can be rerun without
          impacting correct execution. Each step progresses toward the
          outcome while leaving the db in a consistent state.
          If the operation fails mid-way through, it can still be picked up
          again. The database isn't left in an inconsistent state.

        Idempotency:
          The code expects clients to retry migrations without extra
          side-effects. So does this routine. Multiple calls to the
          routine have the same effect on the system state as a single
          call.

        Small number of introspective SQL queries:
          Introspective SQL queries are necessary for checking the state of
          the database. This is required for idempotency and reentrancy. At
          each step, the routine only fires as few introspective SQL
          statements as necessary.
        """
        psql_operations.NotInTransactionMixin()._ensure_not_in_transaction(
            schema_editor
        )
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return

        table_name = model._meta.db_table
        constraint_name = self._get_constraint_name(table_name, column_name)

        is_not_null = self._is_not_null(schema_editor, table_name, column_name)
        constraint_exists = self._constraint_exists(schema_editor, constraint_name)
        if is_not_null and (not constraint_exists):
            return

        if not constraint_exists:
            self._alter_table_not_null_not_valid_constraint(
                schema_editor, table_name, column_name, constraint_name
            )
            self._validate_constraint(schema_editor, table_name, constraint_name)
            self._alter_table_not_null(schema_editor, table_name, column_name)
            self._alter_table_drop_constraint(
                schema_editor, table_name, constraint_name
            )
            return
        elif self._is_constraint_valid(schema_editor, constraint_name):
            if not is_not_null:
                self._alter_table_not_null(schema_editor, table_name, column_name)
            self._alter_table_drop_constraint(
                schema_editor, table_name, constraint_name
            )
            return
        else:
            # Constraint exists and is NOT VALID.
            self._validate_constraint(schema_editor, table_name, constraint_name)
            self._alter_table_not_null(schema_editor, table_name, column_name)
            self._alter_table_drop_constraint(
                schema_editor, table_name, constraint_name
            )
            return

    def set_null(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        model: type[models.Model],
        column_name: str,
    ) -> None:
        psql_operations.NotInTransactionMixin()._ensure_not_in_transaction(
            schema_editor
        )
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return

        table_name = model._meta.db_table
        if not self._is_not_null(schema_editor, table_name, column_name):
            return
        self._alter_table_drop_not_null(schema_editor, table_name, column_name)

    def _alter_table_not_null_not_valid_constraint(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        table_name: str,
        column_name: str,
        constraint_name: str,
    ) -> None:
        cursor = schema_editor.connection.cursor()
        cursor.execute(
            psycopg_sql.SQL(
                ConstraintQueries.ALTER_TABLE_CONSTRAINT_NOT_NULL_NOT_VALID
            ).format(
                table_name=psycopg_sql.Identifier(table_name),
                column_name=psycopg_sql.Identifier(column_name),
                constraint_name=psycopg_sql.Identifier(constraint_name),
            )
        )

    def _is_not_null(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        table_name: str,
        column_name: str,
    ) -> bool:
        cursor = schema_editor.connection.cursor()
        cursor.execute(
            NullabilityQueries.IS_COLUMN_NOT_NULL,
            {"table_name": table_name, "column_name": column_name},
        )
        return bool(cursor.fetchone())

    def _get_constraint_name(self, table_name: str, column_name: str) -> str:
        """
        We need a unique name for the constraint.
        We don't care too much about what the name itself turns out to be. This
        constraint will be deleted at the end of the process anyway.
        Here we just give it some resemblance to the table and column names the
        constraint was created from.
        """
        suffix: str = django_backends_utils.names_digest(  # type:ignore[attr-defined]
            table_name, column_name, length=10
        )
        return f"{table_name[:10]}_{column_name[:10]}_{suffix}"

    def _is_constraint_valid(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        constraint_name: str,
    ) -> bool:
        cursor = schema_editor.connection.cursor()
        cursor.execute(
            ConstraintQueries.CHECK_CONSTRAINT_IS_VALID,
            {"constraint_name": constraint_name},
        )
        return bool(cursor.fetchone())

    def _alter_table_not_null(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        table_name: str,
        column_name: str,
    ) -> None:
        cursor = schema_editor.connection.cursor()
        cursor.execute(
            psycopg_sql.SQL(NullabilityQueries.ALTER_TABLE_SET_NOT_NULL).format(
                table_name=psycopg_sql.Identifier(table_name),
                column_name=psycopg_sql.Identifier(column_name),
            ),
        )

    def _alter_table_drop_not_null(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        table_name: str,
        column_name: str,
    ) -> None:
        cursor = schema_editor.connection.cursor()
        cursor.execute(
            psycopg_sql.SQL(NullabilityQueries.ALTER_TABLE_DROP_NOT_NULL).format(
                table_name=psycopg_sql.Identifier(table_name),
                column_name=psycopg_sql.Identifier(column_name),
            ),
        )

    def _alter_table_drop_constraint(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        table_name: str,
        constraint_name: str,
    ) -> None:
        cursor = schema_editor.connection.cursor()
        cursor.execute(
            psycopg_sql.SQL(ConstraintQueries.ALTER_TABLE_DROP_CONSTRAINT).format(
                table_name=psycopg_sql.Identifier(table_name),
                constraint_name=psycopg_sql.Identifier(constraint_name),
            )
        )

    def _constraint_exists(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        constraint_name: str,
    ) -> bool:
        cursor = schema_editor.connection.cursor()
        cursor.execute(
            ConstraintQueries.CHECK_EXISTING_CONSTRAINT,
            {"constraint_name": constraint_name},
        )
        return bool(cursor.fetchone())

    def _validate_constraint(
        self,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        table_name: str,
        constraint_name: str,
    ) -> None:
        cursor = schema_editor.connection.cursor()
        cursor.execute(
            psycopg_sql.SQL(ConstraintQueries.ALTER_TABLE_VALIDATE_CONSTRAINT).format(
                table_name=psycopg_sql.Identifier(table_name),
                constraint_name=psycopg_sql.Identifier(constraint_name),
            )
        )


class SaferAlterFieldSetNotNull(operation_fields.AlterField):
    def database_forwards(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
    ) -> None:
        NullsManager().set_not_null(
            app_label,
            schema_editor,
            model=to_state.apps.get_model(app_label, self.model_name),
            column_name=self.name,
        )

    def database_backwards(
        self,
        app_label: str,
        schema_editor: base_schema.BaseDatabaseSchemaEditor,
        from_state: migrations.state.ProjectState,
        to_state: migrations.state.ProjectState,
    ) -> None:
        NullsManager().set_null(
            app_label,
            schema_editor,
            model=to_state.apps.get_model(app_label, self.model_name),
            column_name=self.name,
        )

    def describe(self) -> str:
        base = super().describe()
        return (
            f"{base}. Note: Using django_pg_migration_tools "
            f"SaferAlterFieldSetNotNull operation."
        )
