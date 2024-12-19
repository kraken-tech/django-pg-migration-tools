import datetime

import pytest
from django.db import connection, models
from django.utils import timezone

from django_pg_migration_tools import indexes
from tests.example_app.models import DateTimeModel


class TestUniqueIndex:
    app_label = "example_app"

    @pytest.mark.django_db
    def test_non_partial_index(self):
        with connection.schema_editor() as editor:
            index = indexes.UniqueIndex(
                name="recent_dt_idx",
                fields=["dt_field"],
            )
            assert (
                'CREATE UNIQUE INDEX "recent_dt_idx" '
                'ON "example_app_datetimemodel" ("dt_field")'
            ) == str(index.create_sql(DateTimeModel, schema_editor=editor))

            editor.add_index(index=index, model=DateTimeModel)
            with connection.cursor() as cursor:
                assert index.name in connection.introspection.get_constraints(
                    cursor=cursor,
                    table_name=DateTimeModel._meta.db_table,
                )
            editor.remove_index(index=index, model=DateTimeModel)

    @pytest.mark.django_db
    def test_partial_index(self):
        with connection.schema_editor() as editor:
            index = indexes.UniqueIndex(
                name="recent_dt_idx",
                fields=["dt_field"],
                condition=models.Q(
                    dt_field__gt=datetime.datetime(
                        year=2024,
                        month=1,
                        day=1,
                        tzinfo=timezone.get_current_timezone(),
                    ),
                ),
            )
            assert (
                'CREATE UNIQUE INDEX "recent_dt_idx" '
                'ON "example_app_datetimemodel" ("dt_field") WHERE '
                "\"dt_field\" > '2024-01-01 00:00:00-06:00'::timestamptz"
            ) == str(index.create_sql(DateTimeModel, schema_editor=editor))

            editor.add_index(index=index, model=DateTimeModel)
            with connection.cursor() as cursor:
                assert index.name in connection.introspection.get_constraints(
                    cursor=cursor,
                    table_name=DateTimeModel._meta.db_table,
                )
            editor.remove_index(index=index, model=DateTimeModel)

    @pytest.mark.django_db
    def test_partial_int_index(self):
        with connection.schema_editor() as editor:
            index = indexes.UniqueIndex(
                name="partial_pk_idx",
                fields=["id"],
                condition=models.Q(pk__gt=1),
            )
            assert (
                'CREATE UNIQUE INDEX "partial_pk_idx" ON "example_app_datetimemodel" '
                '("id") WHERE "id" > 1'
            ) == str(index.create_sql(DateTimeModel, schema_editor=editor))

            editor.add_index(index=index, model=DateTimeModel)
            with connection.cursor() as cursor:
                assert index.name in connection.introspection.get_constraints(
                    cursor=cursor,
                    table_name=DateTimeModel._meta.db_table,
                )
            editor.remove_index(index=index, model=DateTimeModel)
