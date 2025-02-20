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

.. _guide_adding_a_foreign_key_field:

Adding a Foreign Key Field
--------------------------

When using Django's default ``AddField`` operation, the SQL created has the
following form:

.. code-block:: sql

  ALTER TABLE "foo" ADD COLUMN "bar_id" bigint NULL
  REFERENCES "bar" ("id") DEFERRABLE INITIALLY DEFERRED;

  -- optional: if the field doesn't set index=False
  CREATE INDEX "foo_bar_idx" ON "foo" ("bar_id");

There are two problems:

1. The ``ALTER TABLE`` command takes an AccessExclusive lock, which is the
   highest level of locking. It will block reads and writes on both
   tables.
2. The ``CREATE INDEX`` takes a Share lock which will conflict with
   inserts, updates, and deletes on the table.


Our custom :ref:`SaferAddFieldForeignKey <safer_add_field_foreign_key>`
performs the below queries in order to avoid the two problems above:

.. code-block:: sql

  -- This operation takes an ACCESS EXCLUSIVE LOCK, but for a very short
  -- duration. Adding a nullable field in Postgres doesn't require a full
  -- table scan starting on version 11.
  ALTER TABLE "foo" ADD COLUMN "bar_id" bigint NULL;

  -- This operation takes an ShareUpdateExclusiveLock. It won't block
  -- reads or writes on the table.
  -- [Optional depending on db_index=True]
  SET lock_timeout TO '0';
  CREATE INDEX CONCURRENTLY IF NOT EXISTS bar_id_idx ON foo (bar_id);
  SET lock_timeout TO '10s';

  -- This operation will take a ShareRowExclusive lock on **both** the foo
  -- table and the bar table. This will not block reads, but it
  -- will block insert, updates, and deletes. This will only happen for a
  -- short time, as this operation won't need to scan the whole table.
  ALTER TABLE foo
  ADD CONSTRAINT fk_post_bar FOREIGN KEY (bar_id)
  REFERENCES bar (id)
  DEFERRABLE INITIALLY DEFERRED
  NOT VALID;

  -- This query will take a ShareUpdateExclusive lock on the foo table
  -- (does not block reads nor writes), and a RowShare lock on the bar
  -- table (does not block reads nor writes).
  ALTER TABLE foo VALIDATE CONSTRAINT fk_post_bar;

**NOTE**: Additional queries that are triggered by this operation to
guarantee idempotency have been omitted from the snippet above. The key
take away is that if this migration fails, it can be attempted again and it
will pick up from where it has left off (reentrancy).

**NOTE 2**: If you want to add a ``NOT NULL`` constraint after you have
backfilled the table, you can use the ``SaferAlterFieldSetNotNull``
operation.

.. _guide_how_to_use_safer_add_field_foreign_key:

How to use :ref:`SaferAddFieldForeignKey <safer_add_field_foreign_key>`
_______________________________________________________________________

1. Add a new ForeignKey field to your model

.. code-block:: diff

  +    bar = models.ForeignKey(Bar, null=True, on_delete=models.CASCADE)

2. Make the new migration:

.. code-block:: bash

  ./manage.py makemigrations

3. The only changes you need to perform are:

   1. Swap Django's ``AddField`` for this package's
      ``SaferAddFieldForeignKey`` operation.
   2. Use a non-atomic migration.

.. code-block:: diff

  + from django_pg_migration_tools import operations
  from django.db import migrations


  class Migration(migrations.Migration):
  +   atomic = False

      dependencies = [("myapp", "0042_dependency")]

      operations = [
  -        migrations.AddField(
  +        operations.SaferAddFieldForeignKey(
              model_name="foo",
              name="bar",
              field=models.ForeignKey(
                  null=True,
                  on_delete=django.db.models.deletion.CASCADE,
                  to='myapp.bar',
              ),
          ),
      ]

.. _guide_adding_a_check_constraint:

Adding a Check Constraint
-------------------------

When using Django's default ``AddConstraint`` operation, the SQL created
has the following form:

.. code-block:: sql

  ALTER TABLE foo
  ADD CONSTRAINT bar_not_negative
  CHECK (bar >= 0);

This operation acquires an ``ACCESS EXCLUSIVE`` lock, which is the most
constricted lock in Postgres, blocking any reads, writes, maintenance
activities, and other schema changes on the table.

It will also scan the whole table to make sure there are no violations of
the new constraint. All that while holding onto that lock.

Our custom :ref:`SaferAddCheckConstraint <safer_add_check_constraint>`
operation will instead perform the following:

.. code-block:: sql

  -- Add a NOT VALID constraint.
  -- This type of constraint still works, but only for new writes.
  -- It still requires the AccessExclusive lock, but as it doesn't need to
  -- scan the table, it runs very fast.
  ALTER TABLE foo
  ADD CONSTRAINT bar_not_negative
  CHECK (bar >= 0)
  NOT VALID;

  -- Validate the constraint.
  -- This operation needs to scan the table, but it only holds a
  -- ShareUpdateExclusive lock, which won't block reads or writes.
  ALTER TABLE foo VALIDATE CONSTRAINT bar_not_negative;

Note: The operations above are not inside a transaction. This is by design
to avoid holding the ``ACCESS EXCLUSIVE`` lock from the first ALTER TABLE while
the table scan from the second ALTER TABLE is running. This is also why the
migration file must have ``atomic = False``.

.. _guide_how_to_use_safer_add_check_constraint:

How to use :ref:`SaferAddCheckConstraint <safer_add_check_constraint>`
______________________________________________________________________

1. Add a new Constraint field to your model

.. code-block:: diff

       class Meta:
           constraints = [
               ...
  +            models.CheckConstraint(
  +                condition=Q(bar__gte=0),
  +                name='bar_not_negative',
  +            ),
           ]

2. Make the new migration:

.. code-block:: bash

  ./manage.py makemigrations

3. The only changes you need to perform are:

   1. Swap Django's ``AddConstraint`` for this package's
      ``SaferAddCheckConstraint`` operation.
   2. Use a non-atomic migration.

.. code-block:: diff

  + from django_pg_migration_tools import operations
  from django.db import migrations, models


  class Migration(migrations.Migration):
  +   atomic = False

      dependencies = [("myapp", "0042_dependency")]

      operations = [
  -        migrations.AddConstraint(
  +        operations.SaferAddCheckConstraint(
               model_name="mymodel",
               constraint=models.CheckConstraint(
                   condition=models.Q(bar__gte=0),
                   name="bar_not_negative"
               ),
          ),
      ]

.. _guide_adding_a_one_to_one_field:

Adding a One to One Field
-------------------------

When using Django's default ``AddField`` operation, the SQL created has the
following form:

.. code-block:: sql

  BEGIN;
  --
  -- Add field foo to bar
  --
  ALTER TABLE "myapp_bar"
  ADD COLUMN "foo_id" bigint NULL
  UNIQUE CONSTRAINT "auto_gen_constraint_name"
  REFERENCES "myapp_foo"("id")
  DEFERRABLE INITIALLY DEFERRED;

  SET CONSTRAINTS "auto_gen_constraint_name" IMMEDIATE;
  COMMIT;


The ``ALTER TABLE`` command takes an ``ACCESS EXCLUSIVE`` lock, which is the
highest level of locking. It will block reads and writes on the table. At the
same time, Postgres will serially create the constraint while that lock is
held, which can potentially take a long time.

Our custom :ref:`SaferAddFieldOneToOne <safer_add_field_one_to_one>`
operation will instead perform the following:

.. code-block:: sql

  -- This operation takes an AccessExclusiveLock, but for a very short
  -- duration. Adding a nullable field in Postgres doesn't require a full
  -- table scan starting on version 11.
  ALTER TABLE "myapp_bar" ADD COLUMN IF NOT EXISTS "foo_id" bigint NULL;

  -- This operation takes an ShareUpdateExclusiveLock. It won't block
  -- reads or writes on the table.
  SET lock_timeout TO '0';
  CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "bar_foo_id_uniq" ON "myapp_bar" ("foo_id");
  SET lock_timeout TO '10s';

  -- This operation takes an AccessExclusiveLock, but for a very short
  -- duration as it leverages the unique constraint index above to create
  -- the constraint.
  ALTER TABLE "myapp_bar" ADD CONSTRAINT "bar_foo_id_uniq" UNIQUE USING INDEX "bar_foo_id_uniq";

  -- This operation will take a ShareRowExclusive lock on **both** the foo
  -- table and the bar table. This will not block reads, but it
  -- will block insert, updates, and deletes. This will only happen for a
  -- short time, as this operation won't need to scan the whole table.
  ALTER TABLE "myapp_bar"
  ADD CONSTRAINT "myapp_bar_foo_id_fk" FOREIGN KEY ("foo_id")
  REFERENCES "myapp_foo" ("id")
  DEFERRABLE INITIALLY DEFERRED
  NOT VALID;

  -- This query will take a ShareUpdateExclusive lock on the foo table
  -- (does not block reads nor writes), and a RowShare lock on the bar
  -- table (does not block reads nor writes).
  ALTER TABLE foo VALIDATE CONSTRAINT fk_post_bar;

**NOTE**: Additional queries that are triggered by this operation to
guarantee idempotency have been omitted from the snippet above. The key
take away is that if this migration fails, it can be attempted again and it
will pick up from where it has left off (reentrancy).

**NOTE 2**: If you want to add a ``NOT NULL`` constraint after you have
backfilled the table, you can use the ``SaferAlterFieldSetNotNull``
operation.

.. _guide_how_to_use_safer_add_field_one_to_one:

How to use :ref:`SaferAddFieldOneToOne <safer_add_field_one_to_one>`
____________________________________________________________________

1. Add a new ``OneToOneField`` to your model

.. code-block:: diff

  +    foo = models.OneToOneField(Foo, null=True, on_delete=models.CASCADE)

2. Make the new migration:

.. code-block:: bash

  ./manage.py makemigrations

3. The only changes you need to perform are:

   1. Swap Django's ``AddField`` for this package's
      ``SaferAddFieldOneToOne`` operation.
   2. Use a non-atomic migration.

.. code-block:: diff

  + from django_pg_migration_tools import operations
  from django.db import migrations


  class Migration(migrations.Migration):
  +   atomic = False

      dependencies = [("myapp", "0042_dependency")]

      operations = [
  -        migrations.AddField(
  +        operations.SaferAddFieldOneToOne(
              model_name="bar",
              name="foo",
              field=models.OneToOneField(
                  null=True,
                  on_delete=django.db.models.deletion.CASCADE,
                  to='myapp.foo',
              ),
          ),
      ]

.. _guide_removing_an_index:

Removing an Index
-----------------

Django already provides its own ``RemoveIndexConcurrently`` class, which is
available through ``django.contrib.postres.operations``.

Django's operation performs a naive:

.. code-block:: sql

  DROP INDEX CONCURRENTLY IF EXISTS <idx_name>;

Which has a few problems:

1. It might time out if an existing value of lock_timeout is pre-set.
2. If the operation started but failed because of a lock_timeout error,
   the existing index won't be removed and it will be marked as INVALID.

The second point is usually not widely known. Even with the CONCURRENTLY
condition, the index removal needs to wait on locks, potentially being
interrupted by a lock_timeout thus leaving the existing index marked as
invalid.

Our custom :ref:`SaferRemoveIndexConcurrently <safer_remove_index_concurrently>`
operation will instead perform the following:

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

.. _guide_how_to_use_safer_remove_index_concurrently:

How to use :ref:`SaferRemoveIndexConcurrently <safer_remove_index_concurrently>`
________________________________________________________________________________

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
