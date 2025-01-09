Operations
==========

Provides custom migration operations that help developers perform idempotent and safe schema changes.

Class Definitions
-----------------

.. _safer_add_index_concurrently:
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

    When using Django's default AlterField operation, the SQL created has the
    following form:

    .. code-block:: sql

      ALTER TABLE "foo" ALTER COLUMN "bar" SET NOT NULL;

    This statement will acquire an access exclusive lock on the "foo" table
    while it rescans the table to find potential violations.

    All reads and writes will be blocked.

    This operation leverages Postgres constraints to safely set the column to
    not null. This operation will trigger the following queries:

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

    How to use
    ----------
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

    The below are the queries executed by this operation in order to avoid the
    two problems above:

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

    How to use
    ----------

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

    When using Django's default ``AddConstraint`` operation, the SQL created
    has the following form:

    .. code-block:: sql

      ALTER TABLE foo
      ADD CONSTRAINT bar_not_negative
      CHECK (bar >= 0);

    This operation acquires an AccessExclusive lock, which is the most
    constricted lock in Postgres, blocking any reads, writes, maintanence
    activities, and other schema changes on the table.

    It will also scan the whole table to make sure there are no violations of
    the new constraint. All that while holding onto that lock.

    This safer operation, will instead perform the following:

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
    to avoid holding the AccessExclusive lock from the first ALTER TABLE while
    the table scan from the second ALTER TABLE is running. This is also why the
    migration file must have ``atomic = False``.

    How to use
    ----------

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


    The ``ALTER TABLE`` command takes an AccessExclusive lock, which is the
    highest level of locking. It will block reads and writes on the table.
    At the same time, Postgres will serially create the constraint while that
    lock is held, which can potentially take a long time.

    The below are the queries executed by this operation in order to avoid the
    two problems above:

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

    How to use
    ----------

    1. Add a new OneToOneField to your model

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
