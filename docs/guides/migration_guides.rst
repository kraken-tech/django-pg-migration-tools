Migration Guides
================


.. _guide_adding_an_index:

Adding an Index
---------------

Django already provides its own ``AddIndexConcurrently`` class, which is
available through ``django.contrib.postres.operations``. However, Django's
operation performs a naive:

.. code-block:: sql

  CREATE INDEX CONCURRENTLY <idx> ON <table> USING <method> (<column_name>)

Which has several problems:

1. It might time out due to lock timeouts.
2. It does not remove an invalid index of the same name if it exists.
   Such invalid indexes may exist for various reasons, such as a lock
   timeout when running a ``CREATE INDEX CONCURRENTLY`` operation.
3. It is not idempotent (it does not use ``IF NOT EXISTS``). This might be a
   problem if you want to retry a failed migration, or if you want to add
   the index manually at a convenient time and create the migration later.

The first point is usually not widely known. Even with the concurrently
condition, the index creation needs to wait on locks, potentially being
interrupted by a lock_timeout thus leaving an invalid index behind.

This package provides a drop-in replacement operation
:ref:`SaferAddIndexConcurrently <safer_add_index_concurrently>` that you can
use to address these problems. Under the hood, that operation runs:

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
  CREATE INDEX CONCURRENTLY IF NOT EXISTS foo_idx ON myapp_mymodel (column_name);
  -- Reset lock_timeout to its original value ("1s" as an example).
  SET lock_timeout = '1s';


.. _guide_how_to_use_safer_add_index_concurrently:

How to use :ref:`SaferAddIndexConcurrently <safer_add_index_concurrently>`
__________________________________________________________________________

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


.. _guide_adding_a_unique_constraint:

Adding a Unique Constraint
--------------------------

Django already provides its own ``AddConstraint`` class, which can be used to
create unique constraints. However, Django's operation performs a naive:

.. code-block:: sql

  ALTER TABLE table ADD CONSTRAINT constraint UNIQUE (field);

Which has the following problems:

1. It acquires an ``ACCESS EXCLUSIVE`` lock on the table that blocks reads and
   writes on the table while the unique index that backs up the constraint is
   being created.
2. In turn, it can also be blocked by a existing query. For example, a
   long-running transaction could block this query, which in turn will block
   other queries, creating a potential outage.
3. It doesn't work with retries, as it doesn't check if the constraint
   already exists before attempting the ``ALTER TABLE``.

Our custom :ref:`SaferAddUniqueConstraint <safer_add_unique_constraint>`
constraint operation addresses theses problems by running the following
operations:

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
  CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS foo_unique_idx ON myapp_mymodel (column_name);

  -- Reset lock_timeout to its original value ("1s" as an example).
  SET lock_timeout = '1s';

  -- Perform the ALTER TABLE using the unique index just created.
  ALTER TABLE "myapp_mymodel" ADD CONSTRAINT "foo_unique" UNIQUE USING INDEX "foo_unique_idx";

.. dropdown:: Information about ``deferrable``
    :color: info
    :icon: info

    The ``deferrable`` argument of ``UniqueConstraint`` is respected.

    That is, if set to ``models.Deferrable.DEFERRED``, the ``ALTER TABLE``
    command above will include the suffix ``DEFERRABLE INITIALLY
    DEFERRED``.

    The other value for ``models.Deferrable`` is ``IMMEDIATE``. No changes
    are performed on the ``ALTER TABLE`` statement in this case as
    ``IMMEDIATE`` is the default Postgres behaviour.

.. _guide_how_to_use_safer_add_unique_constraint:

How to use :ref:`SaferAddUniqueConstraint <safer_add_unique_constraint>`
________________________________________________________________________

1. Add the unique constraint to the relevant model as you would normally:

.. code-block:: diff

  +    class Meta:
  +        constraints = (
  +           models.UniqueConstraint(fields=["foo"], name="foo_unique"),
  +        )

2. Make the new migration:

.. code-block:: bash

  ./manage.py makemigrations

3. The only changes you need to perform are:

   - Swap Django's ``AddConstraint`` for this package's
     ``SaferAddUniqueConstraint`` operation.
   - Use a non-atomic migration.

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


.. _guide_setting_a_field_to_not_null:

Setting a Field to NOT NULL
---------------------------

When using Django's default ``AlterField`` operation, the SQL created has the
following form:

.. code-block:: sql

  ALTER TABLE "foo" ALTER COLUMN "bar" SET NOT NULL;

This statement will acquire an access exclusive lock on the "foo" table
while it rescans the table to find potential violations.

All reads and writes will be blocked.

Our custom :ref:`SaferAlterFieldSetNotNull <safer_alter_field_set_not_null>`
operation leverages Postgres constraints to safely set the column to not null.
This operation will trigger the following queries:

.. code-block:: sql

  -- The below still requires ACCESS EXCLUSIVE lock, but doesn't require a
  -- full table scan.
  -- This check will only be applied to new or modified rows, existing rows
  -- won't be validated because of the NOT VALID clause.
  ALTER TABLE foo
  ADD CONSTRAINT bar_not_null
  CHECK (bar IS NOT NULL) NOT VALID;

  -- The below performs a sequential scan, but without an exclusive lock.
  -- Concurrent sessions can read/write.
  -- The operation will require a SHARE UPDATE EXCLUSIVE lock, which will
  -- block only other schema changes and the VACUUM operation.
  ALTER TABLE foo VALIDATE CONSTRAINT bar_not_null;

  -- Requires ACCESS EXCLUSIVE LOCK, but bar_not_null proves that there
  -- is no NULL in this column and a full table scan is not required.
  -- Therefore, the ALTER TABLE command should be fast.
  ALTER TABLE foo ALTER COLUMN bar SET NOT NULL;

  -- The CHECK constraint has fulfilled its obligation and can now
  -- departure.
  -- This takes an ACCESS EXCLUSIVE lock, but should run very fast as it
  -- only has meaningful changes on the catalogue level.
  ALTER TABLE foo DROP CONSTRAINT bar_not_null;

**NOTE**: Additional queries triggered by this operation to guarantee
idempotency have been omitted from the snippet above. The key take away is
that if this migration fails, it can be attempted again and it will pick up
from where it has left off (reentrancy).

.. _guide_how_to_use_safer_alter_field_set_not_null:

How to use :ref:`SaferAlterFieldSetNotNull <safer_alter_field_set_not_null>`
____________________________________________________________________________

1. Make sure that all the rows in the table have already been backfilled
   with a value other than NULL for the column being changed. Also ensure
   that your application code doesn't generate NULL values for that column
   going forward.

2. Set ``null=False`` in your existing field:

.. code-block:: diff

  -    bar = models.IntegerField(null=True)
  +    bar = models.IntegerField(null=False)

3. Make the new migration:

.. code-block:: bash

  ./manage.py makemigrations

4. The only changes you need to perform are: (i) swap Django's
   ``AlterField`` for this package's ``SaferAlterFieldSetNotNull``
   operation, and (ii) use a non-atomic migration.

.. code-block:: diff

  + from django_pg_migration_tools import operations
  from django.db import migrations


  class Migration(migrations.Migration):
  +   atomic = False

      dependencies = [("myapp", "0042_dependency")]

      operations = [
  -        migrations.AlterField(
  +        operations.SaferAlterFieldSetNotNull(
              model_name="foo",
              name="bar",
              field=models.IntegerField()
          ),
      ]
