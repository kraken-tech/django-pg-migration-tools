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
from django.db.models import Index
from django.test import override_settings, utils

from django_pg_migration_tools import operations
from tests.example_app.models import IntModel


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


class AllowDefaultOnly:
    """
    A router that only allows a migration to happen if the instance is the
    "default" instance.
    """

    def allow_migrate(self, db: str, app_label: str, **hints: Any) -> bool:
        return bool(hints["instance"] == "default")


class TestSaferAddIndexConcurrently:
    app_label = "example_app"

    @pytest.mark.django_db
    def test_requires_atomic_false(self):
        project_state = ProjectState()
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
    @override_settings(DATABASE_ROUTERS=[AllowDefaultOnly()])
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
        operation = operations.SaferAddIndexConcurrently(
            "IntModel", index, hints={"instance": "default"}
        )

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
    @override_settings(DATABASE_ROUTERS=[AllowDefaultOnly()])
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
        operation = operations.SaferAddIndexConcurrently(
            # Our migration should only be allowed to run if the instance
            # equals "default" - which isn't the case here.
            "IntModel",
            index,
            hints={"instance": "replica"},
        )

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
