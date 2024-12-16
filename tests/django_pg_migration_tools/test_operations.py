from textwrap import dedent
from typing import Any

import pytest
from django.db import (
    NotSupportedError,
    connection,
    models,
)
from django.db.migrations.state import (
    ModelState,
    ProjectState,
)
from django.db.models import BaseConstraint, Index, Q, UniqueConstraint
from django.test import override_settings, utils

from django_pg_migration_tools import operations
from tests.example_app.models import (
    AnotherCharModel,
    CharIDModel,
    CharModel,
    IntModel,
    NotNullIntFieldModel,
    NullIntFieldModel,
)


try:
    from psycopg import sql as psycopg_sql
except ImportError:  # pragma: no cover
    try:
        from psycopg2 import sql as psycopg_sql  # type: ignore[no-redef]
    except ImportError:
        raise ImportError("Neither psycopg2 nor psycopg (3) is installed.")


_CHECK_INDEX_EXISTS_QUERY = """
SELECT indexname FROM pg_indexes
WHERE (
    tablename = %(table_name)s
    AND indexname = %(index_name)s
);
"""

_CHECK_VALID_INDEX_EXISTS_QUERY = """
SELECT relname
FROM pg_class, pg_index
WHERE (
    pg_index.indisvalid = true
    AND pg_index.indexrelid = pg_class.oid
    AND relname = %(index_name)s
);
"""

_CHECK_CONSTRAINT_EXISTS_QUERY = """
SELECT conname
FROM pg_catalog.pg_constraint cons
JOIN pg_catalog.pg_class class ON class.oid = cons.conrelid
WHERE (
  class.relname = %(table_name)s
  and conname = %(constraint_name)s
);
"""

_CHECK_INVALID_INDEX_EXISTS_QUERY = """
SELECT relname
FROM pg_class, pg_index
WHERE (
    pg_index.indisvalid = false
    AND pg_index.indexrelid = pg_class.oid
    AND relname = %(index_name)s
);
"""

_CREATE_INDEX_QUERY = """
CREATE INDEX "int_field_idx"
ON "example_app_intmodel" ("int_field");
"""

_CREATE_UNIQUE_INDEX_QUERY = """
CREATE UNIQUE INDEX "unique_int_field"
ON "example_app_intmodel" ("int_field");
"""

_SET_INDEX_INVALID = """
UPDATE pg_index
SET indisvalid = false
WHERE indexrelid = (
    SELECT c.oid
    FROM pg_class c
    JOIN pg_namespace n ON c.relnamespace = n.oid
    WHERE c.relname = %(index_name)s
)::regclass;
"""

_SET_LOCK_TIMEOUT = """
SET SESSION lock_timeout = 1000;
"""

_CREATE_CONSTRAINT_QUERY = """
ALTER TABLE "example_app_intmodel"
ADD CONSTRAINT "unique_int_field"
UNIQUE ("int_field");
"""


_DROP_CONSTRAINT_QUERY = """
ALTER TABLE "example_app_intmodel"
DROP CONSTRAINT "unique_int_field";
"""


class NeverAllow:
    """
    A router that never allows a migration to happen.
    """

    def allow_migrate(self, db: str, app_label: str, **hints: Any) -> bool:
        return False


class TestSaferAddIndexConcurrently:
    app_label = "example_app"

    @pytest.mark.django_db
    def test_requires_atomic_false(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()
        operation = operations.SaferAddIndexConcurrently(
            "IntModel", Index(fields=["int_field"], name="int_field_idx")
        )
        with pytest.raises(NotSupportedError):
            with connection.schema_editor(atomic=True) as editor:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

    # Disable the overall test transaction because a concurrent index cannot
    # be triggered/tested inside of a transaction.
    @pytest.mark.django_db(transaction=True)
    def test_add(self):
        with connection.cursor() as cursor:
            # We first create the index and set it to invalid, to make sure it
            # will be removed automatically by the operation before re-creating
            # the index.
            cursor.execute(_CREATE_INDEX_QUERY, {"index_name": "int_field_idx"})
            cursor.execute(_SET_INDEX_INVALID, {"index_name": "int_field_idx"})
            # Also, set the lock_timeout to check it has been returned to
            # its original value once the index creation is completed.
            cursor.execute(_SET_LOCK_TIMEOUT)

        # Prove that the invalid index exists before the operation runs:
        with connection.cursor() as cursor:
            cursor.execute(
                psycopg_sql.SQL(operations.IndexQueries.CHECK_INVALID_INDEX)
                .format(index_name=psycopg_sql.Literal("int_field_idx"))
                .as_string(cursor.connection)
            )
            assert cursor.fetchone()

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()

        # Set the operation that will drop the invalid index and re-create it
        # (without lock timeouts).
        index = Index(fields=["int_field"], name="int_field_idx")
        operation = operations.SaferAddIndexConcurrently("IntModel", index)

        assert operation.describe() == (
            "Concurrently creates index int_field_idx on field(s) "
            "['int_field'] of model IntModel if the index "
            "does not exist. NOTE: Using django_pg_migration_tools "
            "SaferAddIndexConcurrently operation."
        )

        name, args, kwargs = operation.deconstruct()
        assert name == "SaferAddIndexConcurrently"
        assert args == []
        assert kwargs == {"model_name": "IntModel", "index": index}

        operation.state_forwards(self.app_label, new_state)
        assert len(new_state.models[self.app_label, "intmodel"].options["indexes"]) == 1
        assert (
            new_state.models[self.app_label, "intmodel"].options["indexes"][0].name
            == "int_field_idx"
        )
        # Proceed to add the index:
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # Assert the invalid index has been replaced by a valid index.
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_VALID_INDEX_EXISTS_QUERY, {"index_name": "int_field_idx"}
            )
            assert cursor.fetchone()

        # Assert the lock_timeout has been set back to the default (1s)
        with connection.cursor() as cursor:
            cursor.execute(operations.TimeoutQueries.SHOW_LOCK_TIMEOUT)
            assert cursor.fetchone()[0] == "1s"

        # Assert on the sequence of expected SQL queries:
        # 1. Check the original lock_timeout value to be able to restore it
        # later.
        assert queries[0]["sql"] == "SHOW lock_timeout;"
        # 2. Remove the timeout.
        assert queries[1]["sql"] == "SET lock_timeout = '0';"
        # 3. Verify if the index is invalid.
        assert queries[2]["sql"] == dedent("""
            SELECT relname
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = false
                AND pg_index.indexrelid = pg_class.oid
                AND relname = 'int_field_idx'
            );
            """)
        # 4. Drop the index because in this case it was invalid!
        assert queries[3]["sql"] == 'DROP INDEX CONCURRENTLY IF EXISTS "int_field_idx";'
        # 5. Finally create the index concurrently.
        assert (
            queries[4]["sql"]
            == 'CREATE INDEX CONCURRENTLY IF NOT EXISTS "int_field_idx" ON "example_app_intmodel" ("int_field")'
        )
        # 6. Set the timeout back to what it was originally.
        assert queries[5]["sql"] == "SET lock_timeout = '1s';"

        # Reverse the migration to drop the index and verify that the
        # lock_timeout queries are correct.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert reverse_queries[0]["sql"] == "SHOW lock_timeout;"
        assert reverse_queries[1]["sql"] == "SET lock_timeout = '0';"
        assert (
            reverse_queries[2]["sql"]
            == 'DROP INDEX CONCURRENTLY IF EXISTS "int_field_idx"'
        )
        assert reverse_queries[3]["sql"] == "SET lock_timeout = '1s';"

        # Verify the index has been deleted.
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_INDEX_EXISTS_QUERY,
                {"table_name": "example_app_intmodel", "index_name": "int_field_idx"},
            )
            assert not cursor.fetchone()

    @pytest.mark.django_db(transaction=True)
    def test_when_collecting_only(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()

        index = Index(fields=["int_field"], name="int_field_idx")
        operation = operations.SaferAddIndexConcurrently("IntModel", index)

        with connection.schema_editor(atomic=False, collect_sql=True) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        assert len(queries) == 0

        assert len(editor.collected_sql) == 3
        editor.collected_sql[0] = "SET lock_timeout = '0';"
        editor.collected_sql[1] = (
            'CREATE INDEX CONCURRENTLY IF NOT EXISTS "int_field_idx" ON "example_app_intmodel" ("int_field");'
        )
        editor.collected_sql[2] = "SET lock_timeout = '0';"

    # Disable the overall test transaction because a concurrent index cannot
    # be triggered/tested inside of a transaction.
    @pytest.mark.django_db(transaction=True)
    @override_settings(DATABASE_ROUTERS=[NeverAllow()])
    def test_when_not_allowed_to_migrate(self):
        with connection.cursor() as cursor:
            # We first create the index and set it to invalid, to make sure it
            # will not be removed automatically because the operation is not
            # allowed to run.
            cursor.execute(_CREATE_INDEX_QUERY, {"index_name": "int_field_idx"})
            cursor.execute(_SET_INDEX_INVALID, {"index_name": "int_field_idx"})

        # Prove that the invalid index exists before the operation runs:
        with connection.cursor() as cursor:
            cursor.execute(
                psycopg_sql.SQL(operations.IndexQueries.CHECK_INVALID_INDEX)
                .format(index_name=psycopg_sql.Literal("int_field_idx"))
                .as_string(cursor.connection)
            )
            assert cursor.fetchone()

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()

        index = Index(fields=["int_field"], name="int_field_idx")
        operation = operations.SaferAddIndexConcurrently("IntModel", index)

        operation.state_forwards(self.app_label, new_state)
        assert len(new_state.models[self.app_label, "intmodel"].options["indexes"]) == 1
        assert (
            new_state.models[self.app_label, "intmodel"].options["indexes"][0].name
            == "int_field_idx"
        )
        # Proceed to try and add the index:
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # No queries have run, because the migration wasn't allowed to run by
        # the router.
        assert len(queries) == 0

        # Make sure the invalid index was NOT been replaced by a valid index.
        # (because the router didn't allow this migration to run).
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_INVALID_INDEX_EXISTS_QUERY, {"index_name": "int_field_idx"}
            )
            assert cursor.fetchone()


class TestSaferRemoveIndexConcurrently:
    app_label = "example_app"

    @pytest.mark.django_db
    def test_requires_atomic_false(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(CharModel))
        new_state = project_state.clone()
        operation = operations.SaferRemoveIndexConcurrently(
            "charmodel", name="char_field_idx"
        )
        with pytest.raises(NotSupportedError):
            with connection.schema_editor(atomic=True) as editor:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

    # Disable the overall test transaction because a concurrent index operation
    # cannot be triggered/tested inside of a transaction.
    @pytest.mark.django_db(transaction=True)
    def test_remove(self):
        with connection.cursor() as cursor:
            # Set the lock_timeout to check it has been returned to
            # its original value once the index creation is completed.
            cursor.execute(_SET_LOCK_TIMEOUT)

        # Prove that the index exists before running the removal operation.
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_INDEX_EXISTS_QUERY,
                {"table_name": "example_app_charmodel", "index_name": "char_field_idx"},
            )
            assert cursor.fetchone()

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(CharModel))
        new_state = project_state.clone()

        # Verify that the current state has the index we're about to delete.
        assert (
            len(project_state.models[self.app_label, "charmodel"].options["indexes"])
            == 1
        )
        assert (
            project_state.models[self.app_label, "charmodel"].options["indexes"][0].name
            == "char_field_idx"
        )

        # Set the operation that will drop the index concurrently without lock
        # timeouts.
        operation = operations.SaferRemoveIndexConcurrently(
            model_name="charmodel", name="char_field_idx"
        )

        assert operation.describe() == (
            "Concurrently removes index char_field_idx on model charmodel "
            "if the index exists. NOTE: Using django_pg_migration_tools "
            "SaferRemoveIndexConcurrently operation."
        )

        name, args, kwargs = operation.deconstruct()
        assert name == "SaferRemoveIndexConcurrently"
        assert args == []
        assert kwargs == {"model_name": "charmodel", "name": "char_field_idx"}

        # Verify that the index will be removed from the django project state
        # when we run the operation forwards. This is different from actually
        # removing the index from the db.
        operation.state_forwards(self.app_label, new_state)
        assert (
            len(new_state.models[self.app_label, "charmodel"].options["indexes"]) == 0
        )

        # Proceed to remove the index:
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # Prove that the index doesn't exist in the db anymore.
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_INDEX_EXISTS_QUERY,
                {"table_name": "example_app_charmodel", "index_name": "char_field_idx"},
            )
            assert cursor.fetchone() is None

        # Prove that the lock_timeout has been set back to the default (1s)
        with connection.cursor() as cursor:
            cursor.execute(operations.TimeoutQueries.SHOW_LOCK_TIMEOUT)
            assert cursor.fetchone()[0] == "1s"

        # Assert on the sequence of expected SQL queries:
        assert queries[0]["sql"] == "SHOW lock_timeout;"
        assert queries[1]["sql"] == "SET lock_timeout = '0';"
        assert queries[2]["sql"] == 'DROP INDEX CONCURRENTLY IF EXISTS "char_field_idx"'
        assert queries[3]["sql"] == "SET lock_timeout = '1s';"

        # Reverse the migration to re-create the index and verify that the
        # lock_timeout queries are correct.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, new_state, project_state
                )

        assert reverse_queries[0]["sql"] == "SHOW lock_timeout;"
        assert reverse_queries[1]["sql"] == "SET lock_timeout = '0';"
        assert reverse_queries[2]["sql"] == dedent("""
            SELECT relname
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = false
                AND pg_index.indexrelid = pg_class.oid
                AND relname = 'char_field_idx'
            );
            """)
        assert (
            reverse_queries[3]["sql"]
            == 'CREATE INDEX CONCURRENTLY IF NOT EXISTS "char_field_idx" ON "example_app_charmodel" ("char_field")'
        )
        assert reverse_queries[4]["sql"] == "SET lock_timeout = '1s';"

    @pytest.mark.django_db(transaction=True)
    def test_when_collecting_only(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(CharModel))
        new_state = project_state.clone()

        operation = operations.SaferRemoveIndexConcurrently(
            model_name="charmodel", name="char_field_idx"
        )

        with connection.schema_editor(atomic=False, collect_sql=True) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        assert len(queries) == 0

        assert len(editor.collected_sql) == 3
        editor.collected_sql[0] = "SET lock_timeout = '0';"
        editor.collected_sql[1] = (
            'DROP INDEX CONCURRENTLY IF EXISTS "int_field_idx" ON "example_app_intmodel" ("int_field");'
        )
        editor.collected_sql[2] = "SET lock_timeout = '0';"

    # Disable the overall test transaction because a concurrent index cannot
    # be triggered/tested inside of a transaction.
    @pytest.mark.django_db(transaction=True)
    @override_settings(DATABASE_ROUTERS=[NeverAllow()])
    def test_when_not_allowed_to_migrate(self):
        # Prove that the index exists before running the removal operation.
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_INDEX_EXISTS_QUERY,
                {"table_name": "example_app_charmodel", "index_name": "char_field_idx"},
            )
            assert cursor.fetchone()

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(CharModel))
        new_state = project_state.clone()

        operation = operations.SaferRemoveIndexConcurrently(
            "charmodel",
            "char_field_idx",
        )
        # Proceed to try and remove the index:
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # No queries have run, because the migration wasn't allowed to run by
        # the router.
        assert len(queries) == 0

        # Make sure the index is still there and hasn't been removed.
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_INDEX_EXISTS_QUERY,
                {"table_name": "example_app_charmodel", "index_name": "char_field_idx"},
            )
            assert cursor.fetchone()


class TestSaferAddUniqueConstraint:
    app_label = "example_app"

    @pytest.mark.django_db
    def test_requires_atomic_false(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()
        operation = operations.SaferAddUniqueConstraint(
            model_name="intmodel",
            constraint=UniqueConstraint(
                fields=("int_field",),
                name="unique_int_field",
            ),
        )
        with pytest.raises(NotSupportedError):
            with connection.schema_editor(atomic=True) as editor:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # Same for backwards.
        with pytest.raises(NotSupportedError):
            with connection.schema_editor(atomic=True) as editor:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )

    # Disable the overall test transaction because a unique concurrent index
    # cannot be triggered/tested inside of a transaction.
    @pytest.mark.django_db(transaction=True)
    def test_operation_is_idempotent(self):
        with connection.cursor() as cursor:
            # We first create the unique index and set it to INVALID, to make
            # sure it will be removed automatically by the operation before
            # re-creating the unique index from scratch.
            cursor.execute(_CREATE_UNIQUE_INDEX_QUERY)
            cursor.execute(_SET_INDEX_INVALID, {"index_name": "unique_int_field"})
            # Also, set the lock_timeout to check it has been returned to
            # its original value once the unique index creation is completed.
            cursor.execute(_SET_LOCK_TIMEOUT)

        # Prove that the invalid unique index exists before the operation runs:
        with connection.cursor() as cursor:
            cursor.execute(
                psycopg_sql.SQL(operations.IndexQueries.CHECK_INVALID_INDEX)
                .format(index_name=psycopg_sql.Literal("unique_int_field"))
                .as_string(cursor.connection)
            )
            assert cursor.fetchone()

        # Prove that the constraint does **not** already exist.
        with connection.cursor() as cursor:
            cursor.execute(
                psycopg_sql.SQL(operations.ConstraintQueries.CHECK_EXISTING_CONSTRAINT)
                .format(constraint_name=psycopg_sql.Literal("unique_int_field"))
                .as_string(cursor.connection)
            )
            assert not cursor.fetchone()

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()

        operation = operations.SaferAddUniqueConstraint(
            model_name="intmodel",
            constraint=UniqueConstraint(
                fields=("int_field",),
                name="unique_int_field",
            ),
        )

        assert operation.describe() == (
            "Concurrently adds a UNIQUE index unique_int_field on model intmodel "
            "on field(s) ('int_field',) if the index does not exist. Then, adds the "
            "constraint using the just-created index. NOTE: "
            "Using django_pg_migration_tools SaferAddUniqueConstraint operation."
        )

        name, args, kwargs = operation.deconstruct()
        assert name == "SaferAddUniqueConstraint"
        assert args == []
        assert kwargs == {"model_name": "intmodel", "constraint": operation.constraint}

        operation.state_forwards(self.app_label, new_state)
        assert (
            len(new_state.models[self.app_label, "intmodel"].options["constraints"])
            == 1
        )
        assert (
            new_state.models[self.app_label, "intmodel"].options["constraints"][0].name
            == "unique_int_field"
        )

        # Proceed to add the unique index followed by the constraint:
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # Assert the index exists. Note that both index and constraint are
        # "the same thing" in postgres when looking at the table via \d+
        #
        #   Indexes:
        #       "example_table_pkey" PRIMARY KEY, btree (id)
        #       "unique_int_field" UNIQUE CONSTRAINT, btree (int_field)
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_INDEX_EXISTS_QUERY,
                {
                    "table_name": "example_app_intmodel",
                    "index_name": "unique_int_field",
                },
            )
            assert cursor.fetchone()
            cursor.execute(
                _CHECK_CONSTRAINT_EXISTS_QUERY,
                {
                    "table_name": "example_app_intmodel",
                    "constraint_name": "unique_int_field",
                },
            )
            assert cursor.fetchone()

        # Assert the lock_timeout has been set back to the default (1s)
        with connection.cursor() as cursor:
            cursor.execute(operations.TimeoutQueries.SHOW_LOCK_TIMEOUT)
            assert cursor.fetchone()[0] == "1s"

        # Assert on the sequence of expected SQL queries:
        #
        # 1. Check if the constraint already exists.
        assert queries[0]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'unique_int_field';
            """)
        # 2. Check the original lock_timeout value to be able to restore it
        # later.
        assert queries[1]["sql"] == "SHOW lock_timeout;"
        # 3. Remove the timeout.
        assert queries[2]["sql"] == "SET lock_timeout = '0';"
        # 4. Verify if the index is invalid.
        assert queries[3]["sql"] == dedent("""
            SELECT relname
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = false
                AND pg_index.indexrelid = pg_class.oid
                AND relname = 'unique_int_field'
            );
            """)
        # 5. Drop the index because in this case it was invalid!
        assert (
            queries[4]["sql"] == 'DROP INDEX CONCURRENTLY IF EXISTS "unique_int_field";'
        )
        # 6. Finally create the index concurrently.
        assert (
            queries[5]["sql"]
            == 'CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "unique_int_field" ON "example_app_intmodel" ("int_field")'
        )
        # 7. Set the timeout back to what it was originally.
        assert queries[6]["sql"] == "SET lock_timeout = '1s';"

        # 8. Add the table constraint.
        assert (
            queries[7]["sql"]
            == 'ALTER TABLE "example_app_intmodel" ADD CONSTRAINT "unique_int_field" UNIQUE USING INDEX "unique_int_field"'
        )

        # Reverse the migration to drop the index and constraint, and verify
        # that the lock_timeout queries are correct.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )

        # 1. Check that the constraint is still there.
        assert queries[0]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'unique_int_field';
            """)

        # 2. perform the ALTER TABLE.
        assert (
            reverse_queries[1]["sql"]
            == 'ALTER TABLE "example_app_intmodel" DROP CONSTRAINT "unique_int_field"'
        )

        # Verify the constraint doesn't exist any more.
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_CONSTRAINT_EXISTS_QUERY,
                {
                    "table_name": "example_app_intmodel",
                    "constraint_name": "unique_int_field",
                },
            )
            assert not cursor.fetchone()

        # Verify that a second attempt to revert doesn't do anything because
        # the constraint has already been removed.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as second_reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )

        assert len(second_reverse_queries) == 1
        # Check that the constraint isn't there.
        assert second_reverse_queries[0]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'unique_int_field';
            """)

    # Disable the overall test transaction because a unique concurrent index
    # cannot be triggered/tested inside of a transaction.
    @pytest.mark.django_db(transaction=True)
    def test_basic_usage(self):
        # Prove that:
        #   - An invalid index doesn't exist.
        #   - The constraint doesn't exist yet.
        with connection.cursor() as cursor:
            cursor.execute(
                psycopg_sql.SQL(operations.IndexQueries.CHECK_INVALID_INDEX)
                .format(index_name=psycopg_sql.Literal("unique_int_field"))
                .as_string(cursor.connection)
            )
            assert not cursor.fetchone()
            cursor.execute(
                psycopg_sql.SQL(operations.ConstraintQueries.CHECK_EXISTING_CONSTRAINT)
                .format(constraint_name=psycopg_sql.Literal("unique_int_field"))
                .as_string(cursor.connection)
            )
            assert not cursor.fetchone()
            # Also, set the lock_timeout to check it has been returned to
            # its original value once the unique index creation is completed.
            cursor.execute(_SET_LOCK_TIMEOUT)

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()

        operation = operations.SaferAddUniqueConstraint(
            model_name="intmodel",
            constraint=UniqueConstraint(
                fields=("int_field",),
                name="unique_int_field",
            ),
        )
        # Proceed to add the unique index followed by the constraint:
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_INDEX_EXISTS_QUERY,
                {
                    "table_name": "example_app_intmodel",
                    "index_name": "unique_int_field",
                },
            )
            assert cursor.fetchone()
            cursor.execute(
                _CHECK_CONSTRAINT_EXISTS_QUERY,
                {
                    "table_name": "example_app_intmodel",
                    "constraint_name": "unique_int_field",
                },
            )
            assert cursor.fetchone()

        # Assert on the sequence of expected SQL queries:
        #
        # 1. Check if the constraint already exists.
        assert queries[0]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'unique_int_field';
            """)
        # 2. Check the original lock_timeout value to be able to restore it
        # later.
        assert queries[1]["sql"] == "SHOW lock_timeout;"
        # 3. Remove the timeout.
        assert queries[2]["sql"] == "SET lock_timeout = '0';"
        # 4. Verify if the index is invalid.
        assert queries[3]["sql"] == dedent("""
            SELECT relname
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = false
                AND pg_index.indexrelid = pg_class.oid
                AND relname = 'unique_int_field'
            );
            """)
        # 5. Finally create the index concurrently.
        assert (
            queries[4]["sql"]
            == 'CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "unique_int_field" ON "example_app_intmodel" ("int_field")'
        )
        # 6. Set the timeout back to what it was originally.
        assert queries[5]["sql"] == "SET lock_timeout = '1s';"

        # 7. Add the table constraint.
        assert (
            queries[6]["sql"]
            == 'ALTER TABLE "example_app_intmodel" ADD CONSTRAINT "unique_int_field" UNIQUE USING INDEX "unique_int_field"'
        )

        # Reverse the migration to drop the index and constraint, and verify
        # that the lock_timeout queries are correct.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )

        # 1. Check that the constraint is still there.
        assert queries[0]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'unique_int_field';
            """)

        # 2. perform the ALTER TABLE.
        assert (
            reverse_queries[1]["sql"]
            == 'ALTER TABLE "example_app_intmodel" DROP CONSTRAINT "unique_int_field"'
        )

        # Verify the constraint doesn't exist any more.
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_CONSTRAINT_EXISTS_QUERY,
                {
                    "table_name": "example_app_intmodel",
                    "constraint_name": "unique_int_field",
                },
            )
            assert not cursor.fetchone()

    @pytest.mark.django_db(transaction=True)
    def test_when_collecting_only(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()

        operation = operations.SaferAddUniqueConstraint(
            model_name="intmodel",
            constraint=UniqueConstraint(
                fields=("int_field",),
                name="unique_int_field",
            ),
        )
        with connection.schema_editor(atomic=False, collect_sql=True) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        assert len(queries) == 0
        assert len(editor.collected_sql) == 4

        assert editor.collected_sql[0] == "SET lock_timeout = '0';"
        assert (
            editor.collected_sql[1]
            == 'CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "unique_int_field" ON "example_app_intmodel" ("int_field");'
        )
        assert editor.collected_sql[2] == "SET lock_timeout = '0';"
        assert (
            editor.collected_sql[3]
            == 'ALTER TABLE "example_app_intmodel" ADD CONSTRAINT "unique_int_field" UNIQUE USING INDEX "unique_int_field";'
        )

    # Disable the overall test transaction because a unique concurrent index
    # creation followed by a constraint addition cannot be triggered/tested
    # inside of a transaction.
    @pytest.mark.django_db(transaction=True)
    @override_settings(DATABASE_ROUTERS=[NeverAllow()])
    def test_when_not_allowed_to_migrate_by_the_router(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()

        operation = operations.SaferAddUniqueConstraint(
            model_name="intmodel",
            constraint=UniqueConstraint(
                fields=("int_field",),
                name="unique_int_field",
            ),
        )

        # Proceed to try and add the unique index + constraint:
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # No queries have run, because the migration wasn't allowed to run by
        # the router.
        assert len(queries) == 0

        # Try the same for the reverse operation:
        # Proceed to try and add the index + constraint:
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )

        # No queries have run, because the migration wasn't allowed to run by
        # the router.
        assert len(queries) == 0

    @pytest.mark.django_db(transaction=True)
    def test_raises_if_constraint_already_exists(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()

        # Create the constraint so that the operation raises when we try to
        # recreate the constraint with the raise_if_exists flag set to True.
        with connection.cursor() as cursor:
            cursor.execute(_CREATE_CONSTRAINT_QUERY)

        operation = operations.SaferAddUniqueConstraint(
            raise_if_exists=True,
            model_name="intmodel",
            constraint=UniqueConstraint(
                fields=("int_field",),
                name="unique_int_field",
            ),
        )

        with pytest.raises(operations.ConstraintAlreadyExists):
            with connection.schema_editor(atomic=False, collect_sql=False) as editor:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # Drop the constraint. We aren't in a test with transaction, we have
        # to clean up.
        with connection.cursor() as cursor:
            cursor.execute(_DROP_CONSTRAINT_QUERY)

    @pytest.mark.django_db(transaction=True)
    def test_do_nothing_when_asked_not_to_raise_when_constraint_exists(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()

        # Create the constraint. The operation won't raise an error when the
        # constraint already exists because `raise_if_exists` is False.
        with connection.cursor() as cursor:
            cursor.execute(_CREATE_CONSTRAINT_QUERY)

        operation = operations.SaferAddUniqueConstraint(
            raise_if_exists=False,
            model_name="intmodel",
            constraint=UniqueConstraint(
                fields=("int_field",),
                name="unique_int_field",
            ),
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 1

        # Only fired one query to check if the index already exists.
        assert queries[0]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'unique_int_field';
            """)

        # Drop the constraint. As we aren't in a test with transaction, we have
        # to clean up.
        with connection.cursor() as cursor:
            cursor.execute(_DROP_CONSTRAINT_QUERY)

    def test_when_not_unique_constraint(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))

        with pytest.raises(ValueError):
            operations.SaferAddUniqueConstraint(
                model_name="intmodel",
                # This isn't a valid class! There will be a type error here.
                # If the user is using type annotations they will realise the
                # mistake when running mypy. But if not, they will see the
                # exception at runtime.
                constraint=BaseConstraint(  # type: ignore[arg-type]
                    name="test_check_constraint",
                ),
            )

    @pytest.mark.django_db(transaction=True)
    def test_when_condition_on_constraint_only_creates_index(self):
        constraint_name = "partial_unique_int_field"

        # Prove that:
        #   - An invalid index doesn't exist.
        #   - The constraint/index doesn't exist yet.
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_VALID_INDEX_EXISTS_QUERY,
                {"index_name": constraint_name},
            )
            assert not cursor.fetchone()
            # Also, set the lock_timeout to check it has been returned to
            # its original value once the unique index creation is completed.
            cursor.execute(_SET_LOCK_TIMEOUT)

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()

        operation = operations.SaferAddUniqueConstraint(
            model_name="intmodel",
            constraint=UniqueConstraint(
                fields=("int_field",),
                name=constraint_name,
                condition=Q(int_field__gte=2),
            ),
        )
        # Proceed to add the unique index followed by the constraint:
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # Confirm that exists as index
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_INDEX_EXISTS_QUERY,
                {
                    "table_name": "example_app_intmodel",
                    "index_name": constraint_name,
                },
            )
            assert cursor.fetchone()

        # Assert on the sequence of expected SQL queries:
        #
        # 1. Check the original lock_timeout value to be able to restore it
        # later.
        assert queries[0]["sql"] == "SHOW lock_timeout;"
        # 2. Remove the timeout.
        assert queries[1]["sql"] == "SET lock_timeout = '0';"
        # 3. Verify if the index is invalid.
        assert queries[2]["sql"] == dedent(
            f"""
            SELECT relname
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = false
                AND pg_index.indexrelid = pg_class.oid
                AND relname = '{constraint_name}'
            );
            """
        )
        # 4. Finally create the index concurrently.
        assert (
            queries[3]["sql"]
            == f'CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "{constraint_name}" ON "example_app_intmodel" ("int_field") WHERE "int_field" >= 2'
        )
        # 6. Set the timeout back to what it was originally.
        assert queries[4]["sql"] == "SET lock_timeout = '1s';"

        # There are no additional queries
        assert len(queries) == 5

        # Reverse the migration to drop the index and constraint, and verify
        # that the lock_timeout queries are correct.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )

        # 2. perform the ALTER TABLE.
        assert reverse_queries[0]["sql"] == "SHOW lock_timeout;"

        # 3. Remove the timeout.
        assert reverse_queries[1]["sql"] == "SET lock_timeout = '0';"
        # 4. Verify if the index is invalid.
        assert (
            reverse_queries[2]["sql"]
            == f'DROP INDEX CONCURRENTLY IF EXISTS "{constraint_name}"'
        )

        assert reverse_queries[3]["sql"] == "SET lock_timeout = '1s';"

        assert len(reverse_queries) == 4

        # Verify the index representing the constraint doesn't exist any more.
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_INDEX_EXISTS_QUERY,
                {
                    "table_name": "example_app_intmodel",
                    "index_name": constraint_name,
                },
            )
            assert not cursor.fetchone()


class TestBuildPostgresIdentifier:
    def test_happy_path(self):
        assert (
            operations.build_postgres_identifier(
                items=["item1", "item2"], suffix="suffix"
            )
            == "item1_item2_suffix"
        )

    def test_longer_than_63_char(self):
        assert (
            operations.build_postgres_identifier(
                items=["a" * 32, "b" * 32], suffix="suffix"
            )
            == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_bbbbbbbbbbbbbb_bbeb3ff7_suffix"
        )


class TestSaferRemoveUniqueConstraint:
    app_label = "example_app"

    @pytest.mark.django_db
    def test_requires_atomic_false(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(CharModel))
        new_state = project_state.clone()
        operation = operations.SaferRemoveUniqueConstraint(
            model_name="charmodel",
            name="unique_char_field",
        )
        with pytest.raises(NotSupportedError):
            with connection.schema_editor(atomic=True) as editor:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # Same for backwards.
        with pytest.raises(NotSupportedError):
            with connection.schema_editor(atomic=True) as editor:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )

    @pytest.mark.django_db(transaction=True)
    def test_operation(self):
        # Prove that the constraint exists before the operation removes it.
        with connection.cursor() as cursor:
            cursor.execute(
                psycopg_sql.SQL(operations.ConstraintQueries.CHECK_EXISTING_CONSTRAINT)
                .format(constraint_name=psycopg_sql.Literal("unique_char_field"))
                .as_string(cursor.connection)
            )
            assert cursor.fetchone()

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(CharModel))
        new_state = project_state.clone()

        operation = operations.SaferRemoveUniqueConstraint(
            model_name="charmodel",
            name="unique_char_field",
        )

        assert operation.describe() == (
            "Checks if the constraint unique_char_field exists, and if so, removes "
            "it. If the migration is reversed, it will recreate the constraint "
            "using a UNIQUE index. NOTE: Using the django_pg_migration_tools "
            "SaferRemoveIndexConcurrently operation."
        )

        name, args, kwargs = operation.deconstruct()
        assert name == "SaferRemoveUniqueConstraint"
        assert args == []
        assert kwargs == {"model_name": "charmodel", "name": "unique_char_field"}

        operation.state_forwards(self.app_label, new_state)
        assert (
            len(new_state.models[self.app_label, "charmodel"].options["constraints"])
            == 0
        )

        # Proceed to remove the constraint.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # Prove the constraint is not there any longer.
        with connection.cursor() as cursor:
            cursor.execute(
                psycopg_sql.SQL(operations.ConstraintQueries.CHECK_EXISTING_CONSTRAINT)
                .format(constraint_name=psycopg_sql.Literal("unique_char_field"))
                .as_string(cursor.connection)
            )
            assert not cursor.fetchone()

        # Assert on the sequence of expected SQL queries:
        #
        # 1. Check if the constraint exists.
        assert queries[0]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'unique_char_field';
            """)
        # 2. Remove the constraint.
        assert queries[1]["sql"] == (
            'ALTER TABLE "example_app_charmodel" DROP CONSTRAINT "unique_char_field"'
        )
        # Nothing else.
        assert len(queries) == 2

        # Before reversing, set the lock_timeout value so we can observe it
        # being re-set.
        with connection.cursor() as cursor:
            cursor.execute(_SET_LOCK_TIMEOUT)

        # Reverse the migration to recreate the constraint.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, new_state, project_state
                )

        # These will be the same as when creating a constraint safely. I.e.,
        # adding the index concurrently without timeouts, and using this index
        # to create the constraint.
        #
        # 1. Check if the constraint already exists.
        assert reverse_queries[0]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'unique_char_field';
            """)
        # 2. Check the original lock_timeout value to be able to restore it
        # later.
        assert reverse_queries[1]["sql"] == "SHOW lock_timeout;"
        # 3. Remove the timeout.
        assert reverse_queries[2]["sql"] == "SET lock_timeout = '0';"
        # 4. Verify if the index is invalid.
        assert reverse_queries[3]["sql"] == dedent("""
            SELECT relname
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = false
                AND pg_index.indexrelid = pg_class.oid
                AND relname = 'unique_char_field'
            );
            """)
        # 5. Finally create the index concurrently.
        assert (
            reverse_queries[4]["sql"]
            == 'CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "unique_char_field" ON "example_app_charmodel" ("char_field")'
        )
        # 6. Set the timeout back to what it was originally.
        assert reverse_queries[5]["sql"] == "SET lock_timeout = '1s';"

        # 7. Add the table constraint.
        assert (
            reverse_queries[6]["sql"]
            == 'ALTER TABLE "example_app_charmodel" ADD CONSTRAINT "unique_char_field" UNIQUE USING INDEX "unique_char_field"'
        )
        # Nothing else.
        assert len(reverse_queries) == 7

    @pytest.mark.django_db(transaction=True)
    def test_operation_where_condition_on_unique_constraint(self):
        constraint_name = "unique_char_field_with_condition"

        with connection.cursor() as cursor:
            # Set the lock_timeout to check it has been returned to
            # its original value once the index creation is completed.
            cursor.execute(_SET_LOCK_TIMEOUT)

        # Prove that the constraint/index exists before the operation removes it.
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_VALID_INDEX_EXISTS_QUERY,
                {"index_name": constraint_name},
            )
            assert cursor.fetchone()

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(AnotherCharModel))
        new_state = project_state.clone()

        operation = operations.SaferRemoveUniqueConstraint(
            model_name="anothercharmodel",
            name=constraint_name,
        )

        operation.state_forwards(self.app_label, new_state)

        # Proceed to remove the constraint.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # Prove the index is not there any longer.
        with connection.cursor() as cursor:
            cursor.execute(
                _CHECK_VALID_INDEX_EXISTS_QUERY,
                {"index_name": constraint_name},
            )
            assert not cursor.fetchone()

        # Assert on the sequence of expected SQL queries:
        #
        # 1. Check the original lock_timeout value to be able to restore it
        # later.
        assert queries[0]["sql"] == "SHOW lock_timeout;"
        # 2. Remove the timeout.
        assert queries[1]["sql"] == "SET lock_timeout = '0';"

        # 3. Drop the index concurrently.
        assert (
            queries[2]["sql"]
            == f'DROP INDEX CONCURRENTLY IF EXISTS "{constraint_name}"'
        )
        # 4. Set the timeout back to what it was originally.
        assert queries[3]["sql"] == "SET lock_timeout = '1s';"

        assert len(queries) == 4

        # Before reversing, set the lock_timeout value so we can observe it
        # being re-set.
        with connection.cursor() as cursor:
            cursor.execute(_SET_LOCK_TIMEOUT)

        # Reverse the migration to recreate the constraint.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, new_state, project_state
                )

        # These will be the same as when creating a constraint safely. I.e.,
        # adding the index concurrently without timeouts, and using this index
        # to create the constraint.
        #

        # 1. Check the original lock_timeout value to be able to restore it
        # later.
        assert reverse_queries[0]["sql"] == "SHOW lock_timeout;"
        # 2. Remove the timeout.
        assert reverse_queries[1]["sql"] == "SET lock_timeout = '0';"
        # 3. Verify if the index is invalid.
        assert reverse_queries[2]["sql"] == dedent(
            f"""
            SELECT relname
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = false
                AND pg_index.indexrelid = pg_class.oid
                AND relname = '{constraint_name}'
            );
            """
        )
        # 4. Finally create the index concurrently.
        assert (
            reverse_queries[3]["sql"]
            == f'CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "{constraint_name}" ON "example_app_anothercharmodel" ("char_field") WHERE "char_field" IN (\'c\', \'something\')'
        )
        # 5. Set the timeout back to what it was originally.
        assert reverse_queries[4]["sql"] == "SET lock_timeout = '1s';"

        # Nothing else.
        assert len(reverse_queries) == 5

    @pytest.mark.django_db(transaction=True)
    @override_settings(DATABASE_ROUTERS=[NeverAllow()])
    def test_when_not_allowed_to_migrate_by_the_router(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(CharModel))
        new_state = project_state.clone()

        operation = operations.SaferRemoveUniqueConstraint(
            model_name="charmodel",
            name="unique_char_field",
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # No queries have run, because the migration wasn't allowed to run by
        # the router.
        assert len(queries) == 0

        # Try the same for the reverse operation:
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )

        # No queries have run, because the migration wasn't allowed to run by
        # the router.
        assert len(queries) == 0

    @pytest.mark.django_db(transaction=True)
    def test_does_nothing_if_constraint_does_not_exist(self):
        # Remove the constraint so that the migration becomes a noop.
        with connection.cursor() as cursor:
            cursor.execute(
                'ALTER TABLE "example_app_charmodel"'
                'DROP CONSTRAINT "unique_char_field";'
            )

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(CharModel))
        new_state = project_state.clone()

        operation = operations.SaferRemoveUniqueConstraint(
            model_name="charmodel",
            name="unique_char_field",
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        # Checks if the constraint already exists.
        assert queries[0]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'unique_char_field';
            """)
        assert len(queries) == 1


class TestIndexSQLBuilder:
    def test_create_index(self):
        idx_builder = operations.IndexSQLBuilder(
            model_name="mymodel",
            table_name="mytable",
            column_name="mycolumn",
        )
        assert idx_builder.create_sql() == (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            '"mymodel_mycolumn_idx" ON "mytable" ("mycolumn");'
        )

    def test_create_unique_index(self):
        idx_builder = operations.IndexSQLBuilder(
            model_name="mymodel",
            table_name="mytable",
            column_name="mycolumn",
        )
        assert idx_builder.create_sql(unique=True) == (
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
            '"mymodel_mycolumn_idx" ON "mytable" ("mycolumn");'
        )

    def test_drop_index(self):
        idx_builder = operations.IndexSQLBuilder(
            model_name="mymodel",
            table_name="mytable",
            column_name="mycolumn",
        )
        assert idx_builder.remove_sql() == (
            'DROP INDEX CONCURRENTLY IF EXISTS "mymodel_mycolumn_idx";'
        )


class TestSaferAlterFieldSetNotNull:
    app_label = "example_app"

    @pytest.mark.django_db
    def test_requires_atomic_false(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(NullIntFieldModel))
        new_state = project_state.clone()
        operation = operations.SaferAlterFieldSetNotNull(
            model_name="nullintfieldmodel",
            name="int_field",
            field=models.IntegerField(null=False),
        )
        with pytest.raises(NotSupportedError):
            with connection.schema_editor(atomic=True) as editor:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

    @pytest.mark.django_db(transaction=True)
    @override_settings(DATABASE_ROUTERS=[NeverAllow()])
    def test_when_not_allowed_to_migrate_by_the_router(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(NullIntFieldModel))
        new_state = project_state.clone()
        operation = operations.SaferAlterFieldSetNotNull(
            model_name="nullintfieldmodel",
            name="int_field",
            field=models.IntegerField(null=False),
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        # No queries have run, because the migration wasn't allowed to run by
        # the router.
        assert len(queries) == 0

        # Try the same for the reverse operation:
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )

        # No queries have run, because the migration wasn't allowed to run by
        # the router.
        assert len(queries) == 0

    @pytest.mark.django_db(transaction=True)
    def test_operation(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(NullIntFieldModel))
        new_state = project_state.clone()
        operation = operations.SaferAlterFieldSetNotNull(
            model_name="nullintfieldmodel",
            name="int_field",
            field=models.IntegerField(null=False),
        )

        assert operation.describe() == (
            "Alter field int_field on nullintfieldmodel. Note: Using "
            "django_pg_migration_tools SaferAlterFieldSetNotNull operation."
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 6

        assert queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_nullintfieldmodel'::regclass
                AND attname = 'int_field'
                AND attnotnull IS TRUE;
        """)
        assert queries[1]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'example_ap_int_field_59f69830a8';
        """)
        assert queries[2]["sql"] == dedent("""
            ALTER TABLE "example_app_nullintfieldmodel"
            ADD CONSTRAINT "example_ap_int_field_59f69830a8"
            CHECK ("int_field" IS NOT NULL) NOT VALID;
        """)
        assert queries[3]["sql"] == dedent("""
            ALTER TABLE "example_app_nullintfieldmodel"
            VALIDATE CONSTRAINT "example_ap_int_field_59f69830a8";
        """)
        assert queries[4]["sql"] == dedent("""
            ALTER TABLE "example_app_nullintfieldmodel"
            ALTER COLUMN "int_field"
            SET NOT NULL;
        """)
        assert queries[5]["sql"] == dedent("""
            ALTER TABLE "example_app_nullintfieldmodel"
            DROP CONSTRAINT "example_ap_int_field_59f69830a8";
        """)

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(reverse_queries) == 2

        assert reverse_queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_nullintfieldmodel'::regclass
                AND attname = 'int_field'
                AND attnotnull IS TRUE;
        """)
        assert reverse_queries[1]["sql"] == dedent("""
            ALTER TABLE "example_app_nullintfieldmodel"
            ALTER COLUMN "int_field"
            DROP NOT NULL;
        """)

        # Reversing again does nothing apart from checking the field is already
        # nullable.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as second_reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(second_reverse_queries) == 1

        assert second_reverse_queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_nullintfieldmodel'::regclass
                AND attname = 'int_field'
                AND attnotnull IS TRUE;
        """)

    @pytest.mark.django_db(transaction=True)
    def test_when_collecting_only(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(NullIntFieldModel))
        new_state = project_state.clone()
        operation = operations.SaferAlterFieldSetNotNull(
            model_name="nullintfieldmodel",
            name="int_field",
            field=models.IntegerField(null=False),
        )

        with connection.schema_editor(atomic=False, collect_sql=True) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        assert len(queries) == 0

        assert len(editor.collected_sql) == 4

        assert editor.collected_sql[0] == dedent("""
                ALTER TABLE "example_app_nullintfieldmodel"
                ADD CONSTRAINT "example_ap_int_field_59f69830a8"
                CHECK ("int_field" IS NOT NULL) NOT VALID;
            """)
        assert editor.collected_sql[1] == dedent("""
                ALTER TABLE "example_app_nullintfieldmodel"
                VALIDATE CONSTRAINT "example_ap_int_field_59f69830a8";
            """)
        assert editor.collected_sql[2] == dedent("""
                ALTER TABLE "example_app_nullintfieldmodel"
                ALTER COLUMN "int_field"
                SET NOT NULL;
            """)
        assert editor.collected_sql[3] == dedent("""
                ALTER TABLE "example_app_nullintfieldmodel"
                DROP CONSTRAINT "example_ap_int_field_59f69830a8";
            """)

    @pytest.mark.django_db(transaction=True)
    def test_when_field_is_already_not_nullable(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(NotNullIntFieldModel))
        new_state = project_state.clone()
        operation = operations.SaferAlterFieldSetNotNull(
            model_name="notnullintfieldmodel",
            name="int_field",
            field=models.IntegerField(null=False),
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 2

        assert queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_notnullintfieldmodel'::regclass
                AND attname = 'int_field'
                AND attnotnull IS TRUE;
        """)
        assert queries[1]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'example_ap_int_field_147755c69b';
        """)

    @pytest.mark.django_db(transaction=True)
    def test_when_valid_constraint_already_exists(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(NullIntFieldModel))
        new_state = project_state.clone()
        operation = operations.SaferAlterFieldSetNotNull(
            model_name="nullintfieldmodel",
            name="int_field",
            field=models.IntegerField(null=False),
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE example_app_nullintfieldmodel "
                "ADD CONSTRAINT example_ap_int_field_59f69830a8 "
                "CHECK (int_field IS NOT NULL);"
            )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 5

        assert queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_nullintfieldmodel'::regclass
                AND attname = 'int_field'
                AND attnotnull IS TRUE;
        """)
        assert queries[1]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'example_ap_int_field_59f69830a8';
        """)
        assert queries[2]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_constraint
            WHERE
                conname = 'example_ap_int_field_59f69830a8'
                AND convalidated IS TRUE;
        """)
        assert queries[3]["sql"] == dedent("""
            ALTER TABLE "example_app_nullintfieldmodel"
            ALTER COLUMN "int_field"
            SET NOT NULL;
        """)
        assert queries[4]["sql"] == dedent("""
            ALTER TABLE "example_app_nullintfieldmodel"
            DROP CONSTRAINT "example_ap_int_field_59f69830a8";
        """)

    @pytest.mark.django_db(transaction=True)
    def test_when_not_valid_constraint_already_exists(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(NullIntFieldModel))
        new_state = project_state.clone()
        operation = operations.SaferAlterFieldSetNotNull(
            model_name="nullintfieldmodel",
            name="int_field",
            field=models.IntegerField(null=False),
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE example_app_nullintfieldmodel "
                "ADD CONSTRAINT example_ap_int_field_59f69830a8 "
                "CHECK (int_field IS NOT NULL) NOT VALID;"
            )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 6

        assert queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_nullintfieldmodel'::regclass
                AND attname = 'int_field'
                AND attnotnull IS TRUE;
        """)
        assert queries[1]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'example_ap_int_field_59f69830a8';
        """)
        assert queries[2]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_constraint
            WHERE
                conname = 'example_ap_int_field_59f69830a8'
                AND convalidated IS TRUE;
        """)
        assert queries[3]["sql"] == dedent("""
            ALTER TABLE "example_app_nullintfieldmodel"
            VALIDATE CONSTRAINT "example_ap_int_field_59f69830a8";
        """)
        assert queries[4]["sql"] == dedent("""
            ALTER TABLE "example_app_nullintfieldmodel"
            ALTER COLUMN "int_field"
            SET NOT NULL;
        """)
        assert queries[5]["sql"] == dedent("""
            ALTER TABLE "example_app_nullintfieldmodel"
            DROP CONSTRAINT "example_ap_int_field_59f69830a8";
        """)

    @pytest.mark.django_db(transaction=True)
    def test_when_valid_constraint_and_alter_table_already_performed(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(NullIntFieldModel))
        new_state = project_state.clone()
        operation = operations.SaferAlterFieldSetNotNull(
            model_name="nullintfieldmodel",
            name="int_field",
            field=models.IntegerField(null=False),
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE example_app_nullintfieldmodel "
                "ADD CONSTRAINT example_ap_int_field_59f69830a8 "
                "CHECK (int_field IS NOT NULL);"
            )
            cursor.execute(
                "ALTER TABLE example_app_nullintfieldmodel "
                "ALTER COLUMN int_field "
                "SET NOT NULL;"
            )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 4

        assert queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_nullintfieldmodel'::regclass
                AND attname = 'int_field'
                AND attnotnull IS TRUE;
        """)
        assert queries[1]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'example_ap_int_field_59f69830a8';
        """)
        assert queries[2]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_constraint
            WHERE
                conname = 'example_ap_int_field_59f69830a8'
                AND convalidated IS TRUE;
        """)
        assert queries[3]["sql"] == dedent("""
            ALTER TABLE "example_app_nullintfieldmodel"
            DROP CONSTRAINT "example_ap_int_field_59f69830a8";
        """)


class TestSaferSaferAddFieldForeignKey:
    app_label = "example_app"

    @pytest.mark.django_db
    def test_requires_atomic_false(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()
        operation = operations.SaferAddFieldForeignKey(
            model_name="intmodel",
            name="char_model_field",
            field=models.ForeignKey(CharModel, null=True, on_delete=models.CASCADE),
        )
        with pytest.raises(NotSupportedError):
            with connection.schema_editor(atomic=True) as editor:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

    @pytest.mark.django_db(transaction=True)
    def test_when_not_null(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()
        operation = operations.SaferAddFieldForeignKey(
            model_name="intmodel",
            name="char_model_field",
            field=models.ForeignKey(CharModel, null=False, on_delete=models.CASCADE),
        )
        with pytest.raises(
            ValueError, match="Can't safely create a FK field with null=False"
        ):
            with connection.schema_editor(atomic=False) as editor:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

    @pytest.mark.django_db(transaction=True)
    @override_settings(DATABASE_ROUTERS=[NeverAllow()])
    def test_when_not_allowed_to_migrate_by_the_router(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()
        operation = operations.SaferAddFieldForeignKey(
            model_name="intmodel",
            name="char_model_field",
            field=models.ForeignKey(CharModel, null=True, on_delete=models.CASCADE),
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        # No queries have run, because the migration wasn't allowed to run by
        # the router.
        assert len(queries) == 0

        # Try the same for the reverse operation:
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )

        # No queries have run, because the migration wasn't allowed to run by
        # the router.
        assert len(queries) == 0

    @pytest.mark.django_db(transaction=True)
    def test_operation(self):
        with connection.cursor() as cursor:
            # Set the lock_timeout to check it has been returned to
            # its original value once the fk index creation is completed.
            cursor.execute(_SET_LOCK_TIMEOUT)

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()
        operation = operations.SaferAddFieldForeignKey(
            model_name="intmodel",
            name="char_model_field",
            field=models.ForeignKey(CharModel, null=True, on_delete=models.CASCADE),
        )

        assert operation.describe() == (
            "Add field char_model_field to intmodel. Note: Using "
            "django_pg_migration_tools SaferAddFieldForeignKey operation."
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 9

        assert queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert queries[1]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            ADD COLUMN IF NOT EXISTS "char_model_field_id"
            integer NULL;
        """)
        assert queries[2]["sql"] == "SHOW lock_timeout;"
        assert queries[3]["sql"] == "SET lock_timeout = '0';"
        assert queries[4]["sql"] == dedent("""
            SELECT relname
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = false
                AND pg_index.indexrelid = pg_class.oid
                AND relname = 'intmodel_char_model_field_id_idx'
            );
            """)
        assert (
            queries[5]["sql"]
            == 'CREATE INDEX CONCURRENTLY IF NOT EXISTS "intmodel_char_model_field_id_idx" ON "example_app_intmodel" ("char_model_field_id");'
        )
        assert queries[6]["sql"] == "SET lock_timeout = '1s';"
        assert queries[7]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            ADD CONSTRAINT "example_app_intmodel_char_model_field_id_fk" FOREIGN KEY ("char_model_field_id")
            REFERENCES "example_app_charmodel" ("id")
            DEFERRABLE INITIALLY DEFERRED
            NOT VALID;
        """)
        assert queries[8]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            VALIDATE CONSTRAINT "example_app_intmodel_char_model_field_id_fk";
        """)

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(reverse_queries) == 2

        assert reverse_queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert reverse_queries[1]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            DROP COLUMN "char_model_field_id";
        """)

        # Reversing again does nothing apart from checking the field doesn't
        # exist anymore. This check the reverse migration is idempotent.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as second_reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(second_reverse_queries) == 1

        assert reverse_queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)

    @pytest.mark.django_db(transaction=True)
    def test_when_collecting_only(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()
        operation = operations.SaferAddFieldForeignKey(
            model_name="intmodel",
            name="char_model_field",
            field=models.ForeignKey(CharModel, null=True, on_delete=models.CASCADE),
        )
        with connection.schema_editor(atomic=False, collect_sql=True) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )

        assert len(queries) == 0
        assert len(editor.collected_sql) == 6

        assert editor.collected_sql[0] == dedent("""
            ALTER TABLE "example_app_intmodel"
            ADD COLUMN IF NOT EXISTS "char_model_field_id"
            integer NULL;
                                        """)
        assert editor.collected_sql[1] == "SET lock_timeout = '0';"
        assert (
            editor.collected_sql[2]
            == 'CREATE INDEX CONCURRENTLY IF NOT EXISTS "intmodel_char_model_field_id_idx" ON "example_app_intmodel" ("char_model_field_id");'
        )
        assert editor.collected_sql[3] == "SET lock_timeout = '0';"
        assert editor.collected_sql[4] == dedent("""
           ALTER TABLE "example_app_intmodel"
           ADD CONSTRAINT "example_app_intmodel_char_model_field_id_fk" FOREIGN KEY ("char_model_field_id")
           REFERENCES "example_app_charmodel" ("id")
           DEFERRABLE INITIALLY DEFERRED
           NOT VALID;
        """)
        assert editor.collected_sql[5] == dedent("""
           ALTER TABLE "example_app_intmodel"
           VALIDATE CONSTRAINT "example_app_intmodel_char_model_field_id_fk";
        """)

    @pytest.mark.django_db(transaction=True)
    def test_operation_when_column_already_exists(self):
        with connection.cursor() as cursor:
            cursor.execute("""
                ALTER TABLE "example_app_intmodel"
                ADD COLUMN IF NOT EXISTS "char_model_field_id"
                integer NULL;
            """)
            # Also, set the lock_timeout to check it has been returned to
            # its original value once the fk index creation is completed.
            cursor.execute(_SET_LOCK_TIMEOUT)

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()
        operation = operations.SaferAddFieldForeignKey(
            model_name="intmodel",
            name="char_model_field",
            field=models.ForeignKey(CharModel, null=True, on_delete=models.CASCADE),
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 9

        assert queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert queries[1]["sql"] == dedent("""
            SELECT 1
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = true
                AND pg_index.indexrelid = pg_class.oid
                AND relname = 'intmodel_char_model_field_id_idx'
            );
            """)
        assert queries[2]["sql"] == "SHOW lock_timeout;"
        assert queries[3]["sql"] == "SET lock_timeout = '0';"
        assert queries[4]["sql"] == dedent("""
            SELECT relname
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = false
                AND pg_index.indexrelid = pg_class.oid
                AND relname = 'intmodel_char_model_field_id_idx'
            );
            """)
        assert (
            queries[5]["sql"]
            == 'CREATE INDEX CONCURRENTLY IF NOT EXISTS "intmodel_char_model_field_id_idx" ON "example_app_intmodel" ("char_model_field_id");'
        )
        assert queries[6]["sql"] == "SET lock_timeout = '1s';"
        assert queries[7]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            ADD CONSTRAINT "example_app_intmodel_char_model_field_id_fk" FOREIGN KEY ("char_model_field_id")
            REFERENCES "example_app_charmodel" ("id")
            DEFERRABLE INITIALLY DEFERRED
            NOT VALID;
        """)
        assert queries[8]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            VALIDATE CONSTRAINT "example_app_intmodel_char_model_field_id_fk";
        """)

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(reverse_queries) == 2

        assert reverse_queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert reverse_queries[1]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            DROP COLUMN "char_model_field_id";
        """)

    @pytest.mark.django_db(transaction=True)
    def test_operation_when_column_index_already_exists(self):
        with connection.cursor() as cursor:
            cursor.execute("""
                ALTER TABLE "example_app_intmodel"
                ADD COLUMN IF NOT EXISTS "char_model_field_id"
                integer NULL;
            """)
            cursor.execute("""
                CREATE INDEX "intmodel_char_model_field_id_idx"
                ON "example_app_intmodel" ("char_model_field_id");
            """)

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()
        operation = operations.SaferAddFieldForeignKey(
            model_name="intmodel",
            name="char_model_field",
            field=models.ForeignKey(CharModel, null=True, on_delete=models.CASCADE),
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 5

        assert queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert queries[1]["sql"] == dedent("""
            SELECT 1
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = true
                AND pg_index.indexrelid = pg_class.oid
                AND relname = 'intmodel_char_model_field_id_idx'
            );
            """)
        assert queries[2]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'example_app_intmodel_char_model_field_id_fk';
        """)
        assert queries[3]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            ADD CONSTRAINT "example_app_intmodel_char_model_field_id_fk" FOREIGN KEY ("char_model_field_id")
            REFERENCES "example_app_charmodel" ("id")
            DEFERRABLE INITIALLY DEFERRED
            NOT VALID;
        """)
        assert queries[4]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            VALIDATE CONSTRAINT "example_app_intmodel_char_model_field_id_fk";
        """)

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(reverse_queries) == 2

        assert reverse_queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert reverse_queries[1]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            DROP COLUMN "char_model_field_id";
        """)

    @pytest.mark.django_db(transaction=True)
    def test_operation_when_invalid_constraint_already_exists(self):
        with connection.cursor() as cursor:
            cursor.execute("""
                ALTER TABLE "example_app_intmodel"
                ADD COLUMN IF NOT EXISTS "char_model_field_id"
                integer NULL;
            """)
            cursor.execute("""
                CREATE INDEX "intmodel_char_model_field_id_idx"
                ON "example_app_intmodel" ("char_model_field_id");
            """)
            cursor.execute("""
                ALTER TABLE "example_app_intmodel"
                ADD CONSTRAINT "example_app_intmodel_char_model_field_id_fk"
                FOREIGN KEY ("char_model_field_id")
                REFERENCES "example_app_charmodel" ("id")
                DEFERRABLE INITIALLY DEFERRED
                NOT VALID;
            """)

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()
        operation = operations.SaferAddFieldForeignKey(
            model_name="intmodel",
            name="char_model_field",
            field=models.ForeignKey(CharModel, null=True, on_delete=models.CASCADE),
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 5

        assert queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert queries[1]["sql"] == dedent("""
            SELECT 1
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = true
                AND pg_index.indexrelid = pg_class.oid
                AND relname = 'intmodel_char_model_field_id_idx'
            );
            """)
        assert queries[2]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'example_app_intmodel_char_model_field_id_fk';
        """)
        assert queries[3]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_constraint
            WHERE
                conname = 'example_app_intmodel_char_model_field_id_fk'
                AND convalidated IS TRUE;
        """)
        assert queries[4]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            VALIDATE CONSTRAINT "example_app_intmodel_char_model_field_id_fk";
        """)

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(reverse_queries) == 2

        assert reverse_queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert reverse_queries[1]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            DROP COLUMN "char_model_field_id";
        """)

    @pytest.mark.django_db(transaction=True)
    def test_operation_when_valid_constraint_already_exists(self):
        with connection.cursor() as cursor:
            cursor.execute("""
                ALTER TABLE "example_app_intmodel"
                ADD COLUMN IF NOT EXISTS "char_model_field_id"
                integer NULL;
            """)
            cursor.execute("""
                CREATE INDEX "intmodel_char_model_field_id_idx"
                ON "example_app_intmodel" ("char_model_field_id");
            """)
            cursor.execute("""
                ALTER TABLE "example_app_intmodel"
                ADD CONSTRAINT "example_app_intmodel_char_model_field_id_fk"
                FOREIGN KEY ("char_model_field_id")
                REFERENCES "example_app_charmodel" ("id")
                DEFERRABLE INITIALLY DEFERRED;
            """)

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()
        operation = operations.SaferAddFieldForeignKey(
            model_name="intmodel",
            name="char_model_field",
            field=models.ForeignKey(CharModel, null=True, on_delete=models.CASCADE),
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 4

        assert queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert queries[1]["sql"] == dedent("""
            SELECT 1
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = true
                AND pg_index.indexrelid = pg_class.oid
                AND relname = 'intmodel_char_model_field_id_idx'
            );
            """)
        assert queries[2]["sql"] == dedent("""
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conname = 'example_app_intmodel_char_model_field_id_fk';
        """)
        assert queries[3]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_constraint
            WHERE
                conname = 'example_app_intmodel_char_model_field_id_fk'
                AND convalidated IS TRUE;
        """)

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(reverse_queries) == 2

        assert reverse_queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert reverse_queries[1]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            DROP COLUMN "char_model_field_id";
        """)

    @pytest.mark.django_db(transaction=True)
    def test_operation_when_db_index_is_false(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()
        operation = operations.SaferAddFieldForeignKey(
            model_name="intmodel",
            name="char_model_field",
            field=models.ForeignKey(
                CharModel, db_index=False, null=True, on_delete=models.CASCADE
            ),
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 4

        assert queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert queries[1]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            ADD COLUMN IF NOT EXISTS "char_model_field_id"
            integer NULL;
        """)
        assert queries[2]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            ADD CONSTRAINT "example_app_intmodel_char_model_field_id_fk" FOREIGN KEY ("char_model_field_id")
            REFERENCES "example_app_charmodel" ("id")
            DEFERRABLE INITIALLY DEFERRED
            NOT VALID;
        """)
        assert queries[3]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            VALIDATE CONSTRAINT "example_app_intmodel_char_model_field_id_fk";
        """)

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(reverse_queries) == 2

        assert reverse_queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert reverse_queries[1]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            DROP COLUMN "char_model_field_id";
        """)

        # Reversing again does nothing apart from checking the field doesn't
        # exist anymore. This check the reverse migration is idempotent.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as second_reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(second_reverse_queries) == 1

        assert reverse_queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)

    @pytest.mark.django_db(transaction=True)
    def test_operation_when_related_model_does_not_use_int_id(self):
        with connection.cursor() as cursor:
            # Set the lock_timeout to check it has been returned to
            # its original value once the fk index creation is completed.
            cursor.execute(_SET_LOCK_TIMEOUT)

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()
        operation = operations.SaferAddFieldForeignKey(
            model_name="intmodel",
            name="char_id_model_field",
            field=models.ForeignKey(CharIDModel, null=True, on_delete=models.CASCADE),
        )

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 9

        assert queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_id_model_field_id';
        """)
        assert queries[1]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            ADD COLUMN IF NOT EXISTS "char_id_model_field_id"
            varchar(42) NULL;
        """)
        assert queries[2]["sql"] == "SHOW lock_timeout;"
        assert queries[3]["sql"] == "SET lock_timeout = '0';"
        assert queries[4]["sql"] == dedent("""
            SELECT relname
            FROM pg_class, pg_index
            WHERE (
                pg_index.indisvalid = false
                AND pg_index.indexrelid = pg_class.oid
                AND relname = 'intmodel_char_id_model_field_id_idx'
            );
            """)
        assert (
            queries[5]["sql"]
            == 'CREATE INDEX CONCURRENTLY IF NOT EXISTS "intmodel_char_id_model_field_id_idx" ON "example_app_intmodel" ("char_id_model_field_id");'
        )
        assert queries[6]["sql"] == "SET lock_timeout = '1s';"
        assert queries[7]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            ADD CONSTRAINT "example_app_intmodel_char_id_model_field_id_fk" FOREIGN KEY ("char_id_model_field_id")
            REFERENCES "example_app_charidmodel" ("id")
            DEFERRABLE INITIALLY DEFERRED
            NOT VALID;
        """)
        assert queries[8]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            VALIDATE CONSTRAINT "example_app_intmodel_char_id_model_field_id_fk";
        """)

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(reverse_queries) == 2

        assert reverse_queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_id_model_field_id';
        """)
        assert reverse_queries[1]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            DROP COLUMN "char_id_model_field_id";
        """)

        # Reversing again does nothing apart from checking the field doesn't
        # exist anymore. This check the reverse migration is idempotent.
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as second_reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(second_reverse_queries) == 1

        assert reverse_queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_id_model_field_id';
        """)

    @pytest.mark.django_db(transaction=True)
    def test_operation_when_referred_model_is_defined_as_str(self):
        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        project_state.add_model(ModelState.from_model(CharModel))
        new_state = project_state.clone()
        operation = operations.SaferAddFieldForeignKey(
            model_name="intmodel",
            name="char_model_field",
            field=models.ForeignKey(
                "example_app.CharModel",
                null=True,
                on_delete=models.CASCADE,
                db_index=False,
            ),
        )
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as queries:
                operation.database_forwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(queries) == 4

        assert queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert queries[1]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            ADD COLUMN IF NOT EXISTS "char_model_field_id"
            integer NULL;
        """)
        assert queries[2]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            ADD CONSTRAINT "example_app_intmodel_char_model_field_id_fk" FOREIGN KEY ("char_model_field_id")
            REFERENCES "example_app_charmodel" ("id")
            DEFERRABLE INITIALLY DEFERRED
            NOT VALID;
        """)
        assert queries[3]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            VALIDATE CONSTRAINT "example_app_intmodel_char_model_field_id_fk";
        """)

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            with utils.CaptureQueriesContext(connection) as reverse_queries:
                operation.database_backwards(
                    self.app_label, editor, project_state, new_state
                )
        assert len(reverse_queries) == 2

        assert reverse_queries[0]["sql"] == dedent("""
            SELECT 1
            FROM pg_catalog.pg_attribute
            WHERE
                attrelid = 'example_app_intmodel'::regclass
                AND attname = 'char_model_field_id';
        """)
        assert reverse_queries[1]["sql"] == dedent("""
            ALTER TABLE "example_app_intmodel"
            DROP COLUMN "char_model_field_id";
        """)
