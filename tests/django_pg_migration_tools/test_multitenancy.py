"""
Tests for multi-schema (multi-tenant) behavior.

These tests verify that constraint queries are schema-aware and don't
detect constraints from other schemas in multi-tenant setups.
"""

import pytest
from django.db import connection
from django.db.migrations.state import ModelState, ProjectState
from django.db.models import CheckConstraint, Q, UniqueConstraint

from django_pg_migration_tools import operations
from tests.example_app.models import IntModel, ModelWithCheckConstraint


try:
    from psycopg import sql as psycopg_sql
except ImportError:  # pragma: no cover
    try:
        from psycopg2 import sql as psycopg_sql  # type: ignore[no-redef]
    except ImportError:
        raise ImportError("Neither psycopg2 nor psycopg (3) is installed.")


@pytest.mark.django_db(transaction=True)
class TestMultiSchemaConstraints:
    """
    Tests that verify constraint queries are schema-aware.

    These tests create multiple schemas and verify that constraint checks
    only see constraints in the current schema, not in other schemas.
    """

    @pytest.fixture(autouse=True)
    def setup_schemas(self):
        """Create test schemas before each test and clean up after."""
        with connection.cursor() as cursor:
            # Create two test schemas
            cursor.execute("CREATE SCHEMA IF NOT EXISTS tenant1")
            cursor.execute("CREATE SCHEMA IF NOT EXISTS tenant2")

            # Create a test table in both schemas with the same structure
            for schema in ["tenant1", "tenant2"]:
                cursor.execute(f"SET search_path TO {schema}")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS test_table (
                        id SERIAL PRIMARY KEY,
                        value INTEGER NOT NULL
                    )
                    """
                )

        yield

        # Cleanup: drop the test schemas
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")
            cursor.execute("DROP SCHEMA IF EXISTS tenant1 CASCADE")
            cursor.execute("DROP SCHEMA IF EXISTS tenant2 CASCADE")

    @pytest.mark.xfail(
        reason="Constraint queries not yet schema-aware", raises=AssertionError
    )
    def test_check_existing_constraint_only_sees_current_schema(self):
        """
        Test that CHECK_EXISTING_CONSTRAINT only detects constraints in the
        current schema, not in other schemas.
        """
        with connection.cursor() as cursor:
            # Create a constraint in tenant1 schema
            cursor.execute("SET search_path TO tenant1")
            cursor.execute(
                """
                ALTER TABLE test_table
                ADD CONSTRAINT test_constraint CHECK (value > 0)
                """
            )

            # Verify constraint exists in tenant1
            cursor.execute(
                psycopg_sql.SQL(operations.ConstraintQueries.CHECK_EXISTING_CONSTRAINT)
                .format(constraint_name=psycopg_sql.Literal("test_constraint"))
                .as_string(cursor.connection)
            )
            assert cursor.fetchone() is not None, "Constraint should exist in tenant1"

            # Switch to tenant2 schema
            cursor.execute("SET search_path TO tenant2")

            # Verify constraint does NOT exist in tenant2
            cursor.execute(
                psycopg_sql.SQL(operations.ConstraintQueries.CHECK_EXISTING_CONSTRAINT)
                .format(constraint_name=psycopg_sql.Literal("test_constraint"))
                .as_string(cursor.connection)
            )
            assert cursor.fetchone() is None, (
                "Constraint should NOT be visible in tenant2"
            )

    @pytest.mark.xfail(
        reason="Constraint queries not yet schema-aware", raises=AssertionError
    )
    def test_check_constraint_is_valid_only_sees_current_schema(self):
        """
        Test that CHECK_CONSTRAINT_IS_VALID only detects valid constraints
        in the current schema.
        """
        with connection.cursor() as cursor:
            # Create a valid constraint in tenant1
            cursor.execute("SET search_path TO tenant1")
            cursor.execute(
                """
                ALTER TABLE test_table
                ADD CONSTRAINT test_valid_constraint CHECK (value >= 0)
                """
            )

            # Verify it's seen as valid in tenant1
            cursor.execute(
                psycopg_sql.SQL(operations.ConstraintQueries.CHECK_CONSTRAINT_IS_VALID)
                .format(constraint_name=psycopg_sql.Literal("test_valid_constraint"))
                .as_string(cursor.connection)
            )
            assert cursor.fetchone() is not None, (
                "Valid constraint should exist in tenant1"
            )

            # Switch to tenant2
            cursor.execute("SET search_path TO tenant2")

            # Verify it's NOT seen in tenant2
            cursor.execute(
                psycopg_sql.SQL(operations.ConstraintQueries.CHECK_CONSTRAINT_IS_VALID)
                .format(constraint_name=psycopg_sql.Literal("test_valid_constraint"))
                .as_string(cursor.connection)
            )
            assert cursor.fetchone() is None, (
                "Valid constraint should NOT be visible in tenant2"
            )

    @pytest.mark.xfail(
        reason="Constraint queries not yet schema-aware", raises=AssertionError
    )
    def test_check_constraint_is_not_valid_only_sees_current_schema(self):
        """
        Test that CHECK_CONSTRAINT_IS_NOT_VALID only detects NOT VALID
        constraints in the current schema.
        """
        with connection.cursor() as cursor:
            # Create a NOT VALID constraint in tenant1
            cursor.execute("SET search_path TO tenant1")
            cursor.execute(
                """
                ALTER TABLE test_table
                ADD CONSTRAINT test_not_valid_constraint
                CHECK (value <> 0) NOT VALID
                """
            )

            # Verify it's seen as NOT VALID in tenant1
            cursor.execute(
                psycopg_sql.SQL(
                    operations.ConstraintQueries.CHECK_CONSTRAINT_IS_NOT_VALID
                )
                .format(
                    constraint_name=psycopg_sql.Literal("test_not_valid_constraint")
                )
                .as_string(cursor.connection)
            )
            assert cursor.fetchone() is not None, (
                "NOT VALID constraint should exist in tenant1"
            )

            # Switch to tenant2
            cursor.execute("SET search_path TO tenant2")

            # Verify it's NOT seen in tenant2
            cursor.execute(
                psycopg_sql.SQL(
                    operations.ConstraintQueries.CHECK_CONSTRAINT_IS_NOT_VALID
                )
                .format(
                    constraint_name=psycopg_sql.Literal("test_not_valid_constraint")
                )
                .as_string(cursor.connection)
            )
            assert cursor.fetchone() is None, (
                "NOT VALID constraint should NOT be visible in tenant2"
            )

    @pytest.mark.xfail(
        reason="Constraint queries not yet schema-aware",
        raises=operations.ConstraintAlreadyExists,
    )
    def test_unique_constraint_creation_in_second_schema(self):
        """
        Test that SaferAddUniqueConstraint can create a constraint in a second
        schema even when the same constraint name exists in the first schema.
        """
        # Setup: Create the IntModel table in both schemas
        with connection.cursor() as cursor:
            for schema in ["tenant1", "tenant2"]:
                cursor.execute(f"SET search_path TO {schema}")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS example_app_intmodel (
                        id SERIAL PRIMARY KEY,
                        int_field INTEGER NOT NULL
                    )
                    """
                )

            # Create a unique constraint in tenant1
            cursor.execute("SET search_path TO tenant1")

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(IntModel))
        new_state = project_state.clone()

        operation = operations.SaferAddUniqueConstraint(
            model_name="intmodel",
            constraint=UniqueConstraint(
                fields=("int_field",),
                name="unique_int_field_multitenancy_test",
            ),
        )

        # Create constraint in tenant1
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO tenant1")

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            operation.database_forwards(
                "example_app", editor, from_state=project_state, to_state=new_state
            )

        # Verify it exists in tenant1
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO tenant1")
            cursor.execute(
                psycopg_sql.SQL(operations.ConstraintQueries.CHECK_EXISTING_CONSTRAINT)
                .format(
                    constraint_name=psycopg_sql.Literal(
                        "unique_int_field_multitenancy_test"
                    )
                )
                .as_string(cursor.connection)
            )
            assert cursor.fetchone() is not None, "Constraint should exist in tenant1"

            # Now create the SAME constraint in tenant2 - this should succeed
            cursor.execute("SET search_path TO tenant2")

        # This should NOT raise ConstraintAlreadyExists because we're in a different schema
        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            operation.database_forwards(
                "example_app", editor, from_state=project_state, to_state=new_state
            )

        # Verify both schemas have the constraint using a schema-aware query
        # (not the potentially buggy CHECK_EXISTING_CONSTRAINT)
        with connection.cursor() as cursor:
            for schema in ["tenant1", "tenant2"]:
                cursor.execute(f"SET search_path TO {schema}")
                # Use a schema-aware query to verify the constraint exists
                cursor.execute(
                    """
                    SELECT con.conname
                    FROM pg_catalog.pg_constraint con
                    INNER JOIN pg_catalog.pg_namespace nsp ON nsp.oid = con.connamespace
                    WHERE con.conname = %s AND nsp.nspname = current_schema()
                    """,
                    ["unique_int_field_multitenancy_test"],
                )
                assert cursor.fetchone() is not None, (
                    f"Constraint should exist in {schema}"
                )

            # Reset to public schema
            cursor.execute("SET search_path TO public")

    @pytest.mark.xfail(
        reason="Constraint queries not yet schema-aware", raises=AssertionError
    )
    def test_check_constraint_creation_in_second_schema(self):
        """
        Test that SaferAddCheckConstraint can create a constraint in a second
        schema even when the same constraint name exists in the first schema.
        """
        # Setup: Create the ModelWithCheckConstraint table in both schemas
        with connection.cursor() as cursor:
            for schema in ["tenant1", "tenant2"]:
                cursor.execute(f"SET search_path TO {schema}")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS example_app_modelwithcheckconstraint (
                        id SERIAL PRIMARY KEY
                    )
                    """
                )

            cursor.execute("SET search_path TO tenant1")

        project_state = ProjectState()
        project_state.add_model(ModelState.from_model(ModelWithCheckConstraint))
        new_state = project_state.clone()

        operation = operations.SaferAddCheckConstraint(
            model_name="modelwithcheckconstraint",
            constraint=CheckConstraint(
                condition=Q(id__gte=1), name="check_id_multitenancy_test"
            ),
        )

        # Create constraint in tenant1
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO tenant1")

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            operation.database_forwards(
                "example_app", editor, from_state=project_state, to_state=new_state
            )

        # Now create the SAME constraint in tenant2 - this should succeed
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO tenant2")

        with connection.schema_editor(atomic=False, collect_sql=False) as editor:
            operation.database_forwards(
                "example_app", editor, from_state=project_state, to_state=new_state
            )

        # Verify both schemas have the constraint using a schema-aware query
        with connection.cursor() as cursor:
            for schema in ["tenant1", "tenant2"]:
                cursor.execute(f"SET search_path TO {schema}")
                # Use a schema-aware query to verify the constraint exists
                cursor.execute(
                    """
                    SELECT con.conname
                    FROM pg_catalog.pg_constraint con
                    INNER JOIN pg_catalog.pg_namespace nsp ON nsp.oid = con.connamespace
                    WHERE con.conname = %s
                        AND nsp.nspname = current_schema()
                        AND con.convalidated IS TRUE
                    """,
                    ["check_id_multitenancy_test"],
                )
                assert cursor.fetchone() is not None, (
                    f"Check constraint should exist in {schema}"
                )

            # Reset to public schema
            cursor.execute("SET search_path TO public")
