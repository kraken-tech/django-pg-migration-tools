Operations
==========

Provides custom migration operations that help developers perform idempotent and safe schema changes.

Class Definitions
-----------------

.. _safer_add_index_concurrently:
.. py:class:: SaferAddIndexConcurrently(model_name: str, index: models.Index)

    Performs a ``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` without a
    ``lock_timeout`` value to guarantee the index creation won't be affected by
    any pre-set value of ``lock_timeout``.

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param index: Any type of index supported by Django.
    :type index: models.Index

    **Why use SaferAddIndexConcurrently?**
    --------------------------------------

    Refer to the following links:

    - :ref:`Explanation guide behind the need for this class
      <guide_adding_an_index>`.

    - :ref:`Instructions on how to use this class in a migration
      <guide_how_to_use_safer_add_index_concurrently>`.


.. py:class:: SaferRemoveIndexConcurrently(model_name: str, name: str)

    Performs DROP INDEX CONCURRENTLY IF EXISTS without a lock_timeout
    value to guarantee the index removal won't be affected by any pre-set
    value of lock_timeout.

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param name: The name of the index to be deleted.
    :type name: str

    **Why use SaferRemoveIndexConcurrently?**
    -----------------------------------------

    Django already provides its own ``RemoveIndexConcurrently`` class, which is
    available through *django.contrib.postres.operations*.

    Django's operation performs a naive:

    .. code-block:: sql

      DROP INDEX CONCURRENTLY IF EXISTS <idx_name>;

    Which has a few problems:

    1. It might time out if an existing value of lock_timeout is pre-set.
    2. If the operation started but failed because of a lock_timeout error,
       the existing index won't be removed and it will be marked as INVALID.

    Point 2. is usually not widely known. Even with the CONCURRENTLY
    condition, the index removal needs to wait on locks, potentially being
    interrupted by a lock_timeout thus leaving the existing index marked as
    INVALID.

    This custom ``SaferRemoveIndexConcurrently`` operation addresses theses
    problems by running the following operations:

    .. code-block:: sql

      -- Necessary so that we can reset the value later.
      SHOW lock_timeout;
      -- Necessary to avoid lock timeouts. This is a safe operation as
      -- DROP INDEX CONCURRENTLY does not lock concurrent selects, inserts,
      -- updates, or deletes.
      SET lock_timeout = 0;
      -- Drop the index
      DROP INDEX CONCURRENTLY IF EXISTS foo_idx;
      -- Reset lock_timeout to its original value ("1s" as an example).
      SET lock_timeout = '1s';

    How to use
    ----------

    1. Remove the index from the relevant model:

    .. code-block:: diff

          class Meta:
              indexes = (
      -           # Existing index being removed.
      -           models.Index(fields=["foo"], name="foo_idx"),
                  # Another existing index not being removed.
                  models.Index(fields=["bar"], name="bar_idx"),

    2. Make the new migration:

    .. code-block:: bash

      ./manage.py makemigrations

    3. Swap the ``RemoveIndex`` class for ``SaferRemoveIndexConcurrently``.
    Remember to use a non-atomic migration.

    .. code-block:: diff

      + from django_pg_migration_tools import operations
      from django.db import migrations, models


      class Migration(migrations.Migration):
      +   atomic = False

          dependencies = [("myapp", "0042_dependency")]

          operations = [
      -        migrations.RemoveIndex(
      +        operations.SaferRemoveIndexConcurrently(
                  model_name="mymodel",
                  name="foo_idx",
              ),
          ]


.. _safer_add_unique_constraint:
.. py:class:: SaferAddUniqueConstraint(model_name: str, constraint: models.UniqueConstraint, raise_if_exists: bool = True)

    Provides a way to create a unique constraint without blocking reads and
    writes to the table.

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param constraint: A models.UniqueConstraint.
    :type constraint: models.UniqueConstraint
    :param raise_if_exists: Raise a ConstraintAlreadyExists error if the
                            constraint already exists. Defaults to True.
                            You can set this to False if you want to manually
                            create the constraint during low-operation hours on
                            your production environment but you want every
                            other environment (dev/test) to still create the
                            constraint if it doesn't already exist during the
                            migration execution.
    :type raise_if_exists: bool

    **Why use this SaferAddUniqueConstraint operation?**
    -----------------------------------------------------

    Refer to the following links:

    - :ref:`Explanation guide behind the need for this class
      <guide_adding_a_unique_constraint>`.

    - :ref:`Instructions on how to use this class in a migration
      <guide_how_to_use_safer_add_unique_constraint>`.


.. py:class:: SaferRemoveUniqueConstraint(model_name: str, name: str)

    Provides a way to drop a unique constraint in a safer and idempotent
    way.

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param name: The constraint name to be deleted.
    :type name: str

    **Why use this SaferRemoveUniqueConstraint operation?**
    -------------------------------------------------------

    The operation that Django provides (``RemoveConstraint``) has the
    following limitations:

    1. The operation fails if the constraint has already been removed.
    2. When reverting, the alter table statement provided by Django to recreate
       the constraint will block reads and writes on the table.

    This custom operation fixes those problems by:

    - Having a custom forward operation that will only attempt to drop the
      constraint if the constraint exists.
    - Having a custom backward operation that will add the constraint back
      without blocking any reads/writes by creating a unique index concurrently
      first and using it to recreate the constraint. This is achieved through
      the same strategy of
      :ref:`SaferAddIndexConcurrently <safer_add_index_concurrently>`.

    How to use
    ----------

    1. Remove the unique constraint in the relevant model as you would:

    .. code-block:: diff

           class Meta:
      -        constraints = (
      -           models.UniqueConstraint(fields=["foo"], name="foo_unique"),
      -        )

    2. Make the new migration:

    .. code-block:: bash

      ./manage.py makemigrations

    3. The only changes you need to perform are: (i) swap Django's
       ``RemoveConstraint`` for this package's ``SaferRemoveUniqueConstraint``
       operation, and (ii) use a non-atomic migration.

    .. code-block:: diff

      + from django_pg_migration_tools import operations
      from django.db import migrations


      class Migration(migrations.Migration):
      +   atomic = False

          dependencies = [("myapp", "0042_dependency")]

          operations = [
      -        migrations.RemoveConstraint(
      +        operations.SaferRemoveUniqueConstraint(
                  model_name="mymodel",
                  name="foo_unique",
              ),
          ]


.. _safer_alter_field_set_not_null:
.. py:class:: SaferAlterFieldSetNotNull(model_name: str, name: str, field: models.Field)

    Provides a safer way to alter a field to NOT NULL.

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param name: The column name to be set as not null.
    :type name: str
    :param field: The field that is being changed.
    :type field: models.Field

    **Why use this SaferAlterFieldSetNotNull operation?**
    -----------------------------------------------------

    Refer to the following links:

    - :ref:`Explanation guide behind the need for this class
      <guide_setting_a_field_to_not_null>`.

    - :ref:`Instructions on how to use this class in a migration
      <guide_how_to_use_safer_alter_field_set_not_null>`.

.. _safer_add_field_foreign_key:
.. py:class:: SaferAddFieldForeignKey(model_name: str, name: str, field: models.ForeignKey)

    Provides a safer way to add a foreign key field to an existing model

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param name: The column name for the new foreign key.
    :type name: str
    :param field: The foreign key field that is being added.
    :type field: models.ForeignKey

    **Why use this SaferAddFieldForeignKey operation?**
    ---------------------------------------------------

    Refer to the following links:

    - :ref:`Explanation guide behind the need for this class
      <guide_adding_a_foreign_key_field>`.

    - :ref:`Instructions on how to use this class in a migration
      <guide_how_to_use_safer_add_field_foreign_key>`.


.. py:class:: SaferRemoveFieldForeignKey(model_name: str, name: str)

    Provides a safer way to remove a foreign key field.

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param name: The column name for the foreign key field to be deleted.
    :type name: str

    **Why use this SaferRemoveFieldForeignKey operation?**
    ------------------------------------------------------

    The operation that Django provides (``RemoveField``) has the
    following limitations:

    1. The operation fails if the field has already been removed (not
       idempotent).
    2. When reverting, the alter table statement provided by Django to recreate
       the foreign key will block reads and writes on the table.

    This custom operation fixes those problems by:

    - Having a custom forward operation that will only attempt to drop the
      foreign key field if the field exists.
    - Having a custom backward operation that will add the foreign key back
      without blocking any reads/writes. This is achieved through the same
      strategy of :ref:`SaferAddFieldForeignKey <safer_add_field_foreign_key>`.

    How to use
    ----------

    1. Remove the ForeignKey field from your model:

    .. code-block:: diff

      -    bar = models.ForeignKey(Bar, null=True, on_delete=models.CASCADE)

    2. Make the new migration:

    .. code-block:: bash

      ./manage.py makemigrations

    3. The only changes you need to perform are:

       1. Swap Django's ``RemoveField`` for this package's
          ``SaferRemoveFieldForeignKey`` operation.
       2. Use a non-atomic migration.

    .. code-block:: diff

      + from django_pg_migration_tools import operations
      from django.db import migrations


      class Migration(migrations.Migration):
      +   atomic = False

          dependencies = [("myapp", "0042_dependency")]

          operations = [
      -        migrations.RemoveField(
      +        operations.SaferRemoveFieldForeignKey(
                  model_name="mymodel",
                  name="bar",
              ),
          ]

.. _safer_add_check_constraint:
.. py:class:: SaferAddCheckConstraint(model_name: str, constraint: models.CheckConstraint)

    Provides a safer way to add a check constraint to an existing model.

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param constraint: The object representing the constraint to add.
    :type constraint: models.CheckConstraint

    **Why use this SaferAddCheckConstraint operation?**
    ---------------------------------------------------

    - :ref:`Explanation guide behind the need for this class
      <guide_adding_a_check_constraint>`.

    - :ref:`Instructions on how to use this class in a migration
      <guide_how_to_use_safer_add_check_constraint>`.

.. py:class:: SaferRemoveCheckConstraint(model_name: str, name: str)

    Provides a way to drop a check constraint in a safer and idempotent
    way.

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param name: The name of the constraint to be deleted.
    :type name: str

    **Why use this SaferRemoveCheckConstraint operation?**
    ------------------------------------------------------

    The operation that Django provides (``RemoveConstraint``) has the
    following limitations:

    1. The operation fails if the constraint has already been removed.
    2. When reverting, the alter table statement provided by Django to recreate
       the constraint will block reads and writes on the table.

    This custom operation fixes those problems by:

    - Having a custom forward operation that will only attempt to drop the
      constraint if the constraint exists.
    - Having a custom backward operation that will add the constraint back
      without blocking any reads/writes. This is achieved through the same
      strategy of :ref:`SaferAddCheckConstraint <safer_add_check_constraint>`.

    How to use
    ----------

    1. Remove the check constraint in the relevant model as you would:

    .. code-block:: diff

           class Meta:
               constraints = (
                  ...
      -           models.CheckConstraint(
      -             check=~Q(id=42),
      -             name="id_cannot_be_42"
      -           ),
               )

    2. Make the new migration:

    .. code-block:: bash

      ./manage.py makemigrations

    3. The only changes you need to perform are: (i) swap Django's
       ``RemoveConstraint`` for this package's ``SaferRemoveCheckConstraint``
       operation, and (ii) use a non-atomic migration.

    .. code-block:: diff

      + from django_pg_migration_tools import operations
      from django.db import migrations


      class Migration(migrations.Migration):
      +   atomic = False

          dependencies = [("myapp", "0042_dependency")]

          operations = [
      -        migrations.RemoveConstraint(
      +        operations.SaferRemoveCheckConstraint(
                  model_name="mymodel",
                  name="id_cannot_be_42",
              ),
          ]


.. _safer_add_field_one_to_one:

.. py:class:: SaferAddFieldOneToOne(model_name: str, name: str, field: models.OneToOneField)

    Provides a safer way to add a one-to-one field to an existing model

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param name: The column name for the new one-to-one field.
    :type name: str
    :param field: The one-to-one field that is being added.
    :type field: models.OneToOneField

    **Why use this SaferAddFieldOneToOne operation?**
    -------------------------------------------------

    - :ref:`Explanation guide behind the need for this class
      <guide_adding_a_one_to_one_field>`.

    - :ref:`Instructions on how to use this class in a migration
      <guide_how_to_use_safer_add_field_one_to_one>`.
