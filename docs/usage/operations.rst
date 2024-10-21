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

    Django already provides its own ``AddConstraint`` class, which can be
    used to create unique constraints. However, Django's operation performs a
    naive:

    .. code-block:: sql

      ALTER TABLE table ADD CONSTRAINT constraint UNIQUE (field);

    Which has the following problems:

    1. It acquires an ACCESS EXCLUSIVE lock on the table that blocks reads and
       writes on the table.
    2. In turn, it can also be blocked by a existing query. For example, a
       long-running transaction could block this query, which in turn will
       block other queries, creating a potential outage.
    3. It doesn't work with retries, as it doesn't check if the constraint
       already exists before attempting the ALTER TABLE.

    This custom ``SaferAddUniqueConstraint`` operation addresses theses
    problems by running the following operations:

    .. code-block:: sql

      -- Check if the constraint already exists.
      SELECT conname
      FROM pg_catalog.pg_constraint
      WHERE conname = 'foo_unique';

      -- Necessary so that we can reset the value later.
      SHOW lock_timeout;

      -- Necessary to avoid lock timeouts. This is a safe operation as
      -- CREATE UNIQUE INDEX CONCURRENTLY takes a weaker SHARE UPDATE EXCLUSIVE
      -- lock.
      SET lock_timeout = 0;

      -- Check if an INVALID index already exists.
      SELECT relname
      FROM pg_class, pg_index
      WHERE (
          pg_index.indisvalid = false
          AND pg_index.indexrelid = pg_class.oid
          AND relname = 'foo_unique_idx'
      );

      -- Remove the invalid index (only if the previous query returned one).
      DROP INDEX CONCURRENTLY IF EXISTS foo_unique_idx;

      -- Finally create the UNIQUE index
      CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS foo_unique_idx ON myapp_mymodel;

      -- Reset lock_timeout to its original value ("1s" as an example).
      SET lock_timeout = '1s';

      -- Perform the ALTER TABLE using the unique index just created.
      ALTER TABLE "myapp_mymodel" ADD CONSTRAINT "foo_unique" UNIQUE USING INDEX "foo_unique_idx";


    How to use
    ----------

    1. Add the unique constraint to the relevant model as you would normally:

    .. code-block:: diff

      +    class Meta:
      +        constraints = (
      +           models.UniqueConstraint(fields=["foo"], name="foo_unique"),
      +        )

    2. Make the new migration:

    .. code-block:: bash

      ./manage.py makemigrations

    3. The only changes you need to perform are: (i) swap Django's
       ``AddConstraint`` for this package's ``SaferAddUniqueConstraint``
       operation, and (ii) use a non-atomic migration.

    .. code-block:: diff

      + from django_pg_migration_tools import operations
      from django.db import migrations, models


      class Migration(migrations.Migration):
      +   atomic = False

          dependencies = [("myapp", "0042_dependency")]

          operations = [
      -        migrations.AddConstraint(
      +        operations.SaferAddUniqueConstraint(
                  model_name="mymodel",
                  constraint=models.UniqueConstraint(fields=["foo"], name="foo_unique"),
              ),
          ]


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
      the same strategy of py:class:`SaferAddIndexConcurrently`.

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
