Indexes
=======

``UniqueIndex``
---------------

Creates a ``UNIQUE`` postgres index.

Django doesn't have an operation to create a unique index.

The next best thing Django provides is a ``UniqueConstraint``.
However, unique constraints in Postgres can't be partial (i.e., have
conditions) so there is a gap in functionality that is covered by this special
index class.

++++++++++
How to use
++++++++++

The interface is the same as Django's models.Index class:

.. code-block:: diff

    from django.db import models
  + from django_pg_migration_tools import indexes

    class CharModel(models.Model):
        char_field = models.CharField(max_length=42)

        class Meta:
            indexes = [
                ...,
  +             indexes.UniqueIndex(
  +                 name="unique_char_unless_foo",
  +                 fields=["char_field"],
  +                 condition=~models.Q(char_field="foo")
  +             )
            ]


    class IntModel(models.Model):
        int_field = models.IntegerField()

        class Meta:
            indexes = [
                ...,
  +             indexes.UniqueIndex(
  +                 name="unique_int",
  +                 fields=["int_field"],
  +             )
            ]


Note: If you are creating a new unique index in a table that already exists,
you can use the :ref:`SaferAddIndexConcurrently <safer_add_index_concurrently>`
operation.

.. code-block:: diff

    import django_pg_migration_tools.indexes
    from django.db import migrations, models

  + from django_pg_migration_tools import operations


    class Migration(migrations.Migration):
  +     atomic = False

        dependencies = [
            ('myapp', '0001_initial'),
        ]

        operations = [
  -         migrations.AddIndex(
  +         operations.SaferAddIndexConcurrently(
                model_name='charmodel',
                index=django_pg_migration_tools.indexes.UniqueIndex(
                    condition=models.Q(('char_field', 'foo'), _negated=True),
                    fields=['char_field'],
                    name='unique_char_unless_foo',
                ),
            ),
  -         migrations.AddIndex(
  +         operations.SaferAddIndexConcurrently(
                model_name='intmodel',
                index=django_pg_migration_tools.indexes.UniqueIndex(
                    fields=['int_field'],
                    name='unique_int',
                ),
            ),
        ]
