from __future__ import annotations

from textwrap import dedent


class TimeoutQueries:
    SHOW_LOCK_TIMEOUT = "SHOW lock_timeout;"
    SET_LOCK_TIMEOUT = "SET lock_timeout = {lock_timeout};"


class IndexQueries:
    CHECK_INVALID_INDEX = dedent("""
        SELECT relname
        FROM pg_class, pg_index
        WHERE (
            pg_index.indisvalid = false
            AND pg_index.indexrelid = pg_class.oid
            AND relname = {index_name}
        );
    """)
    DROP_INDEX = "DROP INDEX CONCURRENTLY IF EXISTS {index_name};"
    CHECK_VALID_INDEX = dedent("""
        SELECT 1
        FROM pg_class, pg_index
        WHERE (
            pg_index.indisvalid = true
            AND pg_index.indexrelid = pg_class.oid
            AND relname = {index_name}
        );
    """)


class ConstraintQueries:
    CHECK_EXISTING_CONSTRAINT = dedent("""
        SELECT conname
        FROM pg_catalog.pg_constraint
        WHERE conname = {constraint_name};
    """)

    CHECK_CONSTRAINT_IS_VALID = dedent("""
        SELECT 1
        FROM pg_catalog.pg_constraint
        WHERE
            conname = {constraint_name}
            AND convalidated IS TRUE;
    """)

    CHECK_CONSTRAINT_IS_NOT_VALID = dedent("""
        SELECT 1
        FROM pg_catalog.pg_constraint
        WHERE
            conname = {constraint_name}
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

    ALTER_TABLE_ADD_NOT_VALID_FK = dedent("""
        ALTER TABLE {table_name}
        ADD CONSTRAINT {constraint_name} FOREIGN KEY ({column_name})
        REFERENCES {referred_table_name} ({referred_column_name})
        DEFERRABLE INITIALLY DEFERRED
        NOT VALID;
    """)


class ColumnQueries:
    ALTER_TABLE_ADD_NULL_COLUMN = dedent("""
        ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS {column_name}
        {column_type} NULL;
    """)
    ALTER_TABLE_DROP_COLUMN = dedent("""
        ALTER TABLE {table_name}
        DROP COLUMN {column_name};
    """)
    CHECK_COLUMN_EXISTS = dedent("""
        SELECT 1
        FROM pg_catalog.pg_attribute
        WHERE
            attrelid = {table_name}::regclass
            AND attname = {column_name};
    """)


class NullabilityQueries:
    IS_COLUMN_NOT_NULL = dedent("""
        SELECT 1
        FROM pg_catalog.pg_attribute
        WHERE
            attrelid = {table_name}::regclass
            AND attname = {column_name}
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
