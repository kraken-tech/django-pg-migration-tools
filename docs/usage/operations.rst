Operations
==========

Provides custom migration operations that help developers perform idempotent and safe schema changes.

Class Definitions
-----------------

.. py:class:: SaferAddIndexConcurrently(model_name: str, index: models.Index)

    Performs CREATE INDEX CONCURRENTLY IF NOT EXISTS without a lock_timeout
    value to guarantee the index creation won't be affected by any pre-set
    value of lock_timeout.

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param index: Any type of index supported by Django.
    :type index: models.Index

    **Why use this SaferAddIndexConcurrently operation?**
    -----------------------------------------------------

    Django already provides its own ``AddIndexConcurrently`` class, which is
    available through *django.contrib.postres.operations*.

    Django's operation performs a naive:

    .. code-block:: sql

      CREATE INDEX CONCURRENTLY <idx> ON <table> USING <method>

    Which has several problems:

    1. It is not idempotent (it does not use IF NOT EXISTS). This might be a
       problem if you want to retry a failed migration, or if you want to add
       the index manually at a convenient time and create the migration later.
    2. It might time out due to lock timeouts.
    3. It does not remove an INVALID index of the same name if it exists.
       Such INVALID indexes may exist for various reasons, such as a lock
       timeout when running a CREATE INDEX CONCURRENTLY operation.

    Point 2. is usually not widely known. Even with the CONCURRENTLY
    condition, the index creation needs to wait on locks, potentially being
    interrupted by a lock_timeout thus leaving an INVALID index behind.

    This custom ``SaferAddIndexConcurrently`` operation addresses theses
    problems by running the following operations:

    .. code-block:: sql

      -- Necessary so that we can reset the value later.
      SHOW lock_timeout;
      -- Necessary to avoid lock timeouts. This is a safe operation as
      -- CREATE INDEX CONCURRENTLY takes a weak SHARE UPDATE EXCLUSIVE lock.
      SET lock_timeout = 0;
      -- Check if an INVALID index already exists.
      SELECT relname
      FROM pg_class, pg_index
      WHERE (
          pg_index.indisvalid = false
          AND pg_index.indexrelid = pg_class.oid
          AND relname = 'foo_idx'
      );
      -- Remove the invalid index (only if the previous query returned one).
      DROP INDEX CONCURRENTLY IF EXISTS foo_idx;
      -- Finally create the index
      CREATE INDEX CONCURRENTLY IF NOT EXISTS foo_idx ON myapp_mymodel;
      -- Reset lock_timeout to its original value ("1s" as an example).
      SET lock_timeout = '1s';

    How to use
    ----------

    1. Add the index to the relevant model:

    .. code-block:: diff

          class Meta:
              indexes = (
      +           # new index
      +           models.Index(fields=["foo"], name="foo_idx"),
                  # existing index
                  models.Index(fields=["bar"], name="bar_idx"),

    2. Make the new migration:

    .. code-block:: bash

      ./manage.py makemigrations

    3. Swap the ``AddIndex`` class for our own ``SaferAddIndexConcurrently`` class.
    Remember to use a non-atomic migration.

    .. code-block:: diff

      + from django_pg_migration_tools import operations
      from django.db import migrations, models


      class Migration(migrations.Migration):
      +   atomic = False

          dependencies = [("myapp", "0042_dependency")]

          operations = [
      -        migrations.AddIndex(
      +        operations.SaferAddIndexConcurrently(
                  model_name="mymodel",
                  index=models.Index(fields=["foo"], name="foo_idx"),
              ),
          ]



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
