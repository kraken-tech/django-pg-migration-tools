from textwrap import dedent
from typing import Any

import pytest
from django.db import (
    NotSupportedError,
    connection,
)
from django.db.migrations.state import (
    ModelState,
    ProjectState,
)
from django.db.models import CheckConstraint, Index, Q, UniqueConstraint
from django.test import override_settings, utils

from django_pg_migration_tools import operations
from tests.example_app.models import CharModel, IntModel


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
                operations.SaferAddIndexConcurrently.CHECK_INVALID_INDEX_QUERY,
                {"index_name": "int_field_idx"},
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
            cursor.execute(operations.SaferAddIndexConcurrently.SHOW_LOCK_TIMEOUT_QUERY)
            assert cursor.fetchone()[0] == "1s"

        # Assert on the sequence of expected SQL queries:
        # 1. Check the original lock_timeout value to be able to restore it
        # later.
        assert queries[0]["sql"] == "SHOW lock_timeout;"
        # 2. Remove the timeout.
        assert queries[1]["sql"] == "SET lock_timeout = 0;"
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
        assert reverse_queries[1]["sql"] == "SET lock_timeout = 0;"
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
                operations.SaferAddIndexConcurrently.CHECK_INVALID_INDEX_QUERY,
                {"index_name": "int_field_idx"},
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
            cursor.execute(
                operations.SaferRemoveIndexConcurrently.SHOW_LOCK_TIMEOUT_QUERY
            )
            assert cursor.fetchone()[0] == "1s"

        # Assert on the sequence of expected SQL queries:
        assert queries[0]["sql"] == "SHOW lock_timeout;"
        assert queries[1]["sql"] == "SET lock_timeout = 0;"
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
        assert reverse_queries[1]["sql"] == "SET lock_timeout = 0;"
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
                operations.SaferAddUniqueConstraint.CHECK_INVALID_INDEX_QUERY,
                {"index_name": "unique_int_field"},
            )
            assert cursor.fetchone()

        # Prove that the constraint does **not** already exist.
        with connection.cursor() as cursor:
            cursor.execute(
                operations.SaferAddUniqueConstraint._CHECK_EXISTING_CONSTRAINT_QUERY,
                {"constraint_name": "unique_int_field"},
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
            cursor.execute(operations.BaseIndexOperation.SHOW_LOCK_TIMEOUT_QUERY)
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
        assert queries[2]["sql"] == "SET lock_timeout = 0;"
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
                operations.SaferAddUniqueConstraint.CHECK_INVALID_INDEX_QUERY,
                {"index_name": "unique_int_field"},
            )
            assert not cursor.fetchone()
            cursor.execute(
                operations.SaferAddUniqueConstraint._CHECK_EXISTING_CONSTRAINT_QUERY,
                {"constraint_name": "unique_int_field"},
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
        assert queries[2]["sql"] == "SET lock_timeout = 0;"
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

    # Disable the overall test transaction because a unqiue concurrent index
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
                # This isn't a valid class! There will be a type error here,
                # but we want to cover for people not using types as well.
                constraint=CheckConstraint(  # type: ignore[arg-type]
                    name="test_check_constraint",
                    condition=Q(),
                ),
            )
