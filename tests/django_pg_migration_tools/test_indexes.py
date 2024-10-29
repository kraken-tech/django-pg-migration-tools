import pytest
from django.db import connection, models

from django_pg_migration_tools import indexes
from tests.example_app.models import CharModel


class TestUniqueIndex:
    app_label = "example_app"

    @pytest.mark.django_db
    def test_non_partial_index(self):
        with connection.schema_editor() as editor:
            index = indexes.UniqueIndex(
                name="recent_dt_idx",
                fields=["char_field"],
            )
            assert (
                'CREATE UNIQUE INDEX "recent_dt_idx" '
                'ON "example_app_charmodel" ("char_field")'
            ) == str(index.create_sql(CharModel, schema_editor=editor))

            editor.add_index(index=index, model=CharModel)
            with connection.cursor() as cursor:
                assert index.name in connection.introspection.get_constraints(
                    cursor=cursor,
                    table_name=CharModel._meta.db_table,
                )
            editor.remove_index(index=index, model=CharModel)

    @pytest.mark.django_db
    def test_partial_index(self):
        with connection.schema_editor() as editor:
            index = indexes.UniqueIndex(
                name="partial_char_field_idx",
                fields=["char_field"],
                condition=~models.Q(char_field="foo"),
            )
            assert (
                'CREATE UNIQUE INDEX "partial_char_field_idx" '
                'ON "example_app_charmodel" ("char_field") WHERE NOT ('
                "\"char_field\" = 'foo')"
            ) == str(index.create_sql(CharModel, schema_editor=editor))

            editor.add_index(index=index, model=CharModel)
            with connection.cursor() as cursor:
                assert index.name in connection.introspection.get_constraints(
                    cursor=cursor,
                    table_name=CharModel._meta.db_table,
                )
            editor.remove_index(index=index, model=CharModel)

    @pytest.mark.django_db
    def test_partial_int_index(self):
        with connection.schema_editor() as editor:
            index = indexes.UniqueIndex(
                name="partial_pk_idx",
                fields=["id"],
                condition=models.Q(pk__gt=1),
            )
            assert (
                'CREATE UNIQUE INDEX "partial_pk_idx" ON "example_app_charmodel" '
                '("id") WHERE "id" > 1'
            ) == str(index.create_sql(CharModel, schema_editor=editor))

            editor.add_index(index=index, model=CharModel)
            with connection.cursor() as cursor:
                assert index.name in connection.introspection.get_constraints(
                    cursor=cursor,
                    table_name=CharModel._meta.db_table,
                )
            editor.remove_index(index=index, model=CharModel)
