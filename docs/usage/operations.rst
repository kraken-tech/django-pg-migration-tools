Operations
==========

Provides custom migration operations that help developers perform idempotent and safe schema changes.

Class Definitions
-----------------

.. _safer_add_index_concurrently:
.. py:class:: SaferAddIndexConcurrently(model_name: str, index: models.Index)

    Performs a ``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` without a
    ``lock_timeout`` value to guarantee the index creation won't be affected by
    any pre-set value of ``lock_timeout``. Use
    :ref:`index_lock_timeout <index_lock_timeout>` to choose a different
    ``lock_timeout``.

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


.. _safer_remove_index_concurrently:

.. py:class:: SaferRemoveIndexConcurrently(model_name: str, name: str)

    Performs DROP INDEX CONCURRENTLY IF EXISTS without a lock_timeout
    value to guarantee the index removal won't be affected by any pre-set
    value of lock_timeout. Use
    :ref:`index_lock_timeout <index_lock_timeout>` to choose a different
    lock_timeout.

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param name: The name of the index to be deleted.
    :type name: str

    **Why use SaferRemoveIndexConcurrently?**
    -----------------------------------------

    - :ref:`Explanation guide behind the need for this class
      <guide_removing_an_index>`.

    - :ref:`Instructions on how to use this class in a migration
      <guide_how_to_use_safer_remove_index_concurrently>`.


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

.. _safer_remove_unique_constraint:

.. py:class:: SaferRemoveUniqueConstraint(model_name: str, name: str)

    Provides a way to drop a unique constraint in a safer and idempotent
    way.

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param name: The constraint name to be deleted.
    :type name: str

    **Why use this SaferRemoveUniqueConstraint operation?**
    -------------------------------------------------------

    - :ref:`Explanation guide behind the need for this class
      <guide_removing_a_unique_constraint>`.

    - :ref:`Instructions on how to use this class in a migration
      <guide_how_to_use_safer_remove_unique_constraint>`.

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

.. _safer_remove_field_foreign_key:

.. py:class:: SaferRemoveFieldForeignKey(model_name: str, name: str)

    Provides a safer way to remove a foreign key field.

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param name: The column name for the foreign key field to be deleted.
    :type name: str

    **Why use this SaferRemoveFieldForeignKey operation?**
    ------------------------------------------------------

    - :ref:`Explanation guide behind the need for this class
      <guide_removing_a_foreign_key_field>`.

    - :ref:`Instructions on how to use this class in a migration
      <guide_how_to_use_safer_remove_field_foreign_key>`.

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

.. _safer_remove_check_constraint:

.. py:class:: SaferRemoveCheckConstraint(model_name: str, name: str)

    Provides a way to drop a check constraint in a safer and idempotent
    way.

    :param model_name: Model name in lowercase without underscores.
    :type model_name: str
    :param name: The name of the constraint to be deleted.
    :type name: str

    **Why use this SaferRemoveCheckConstraint operation?**
    ------------------------------------------------------

    - :ref:`Explanation guide behind the need for this class
      <guide_removing_a_check_constraint>`.

    - :ref:`Instructions on how to use this class in a migration
      <guide_how_to_use_safer_remove_check_constraint>`.

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

Function Definitions
--------------------

.. _index_lock_timeout:

.. py:function:: index_lock_timeout(lock_timeout: datetime.timedelta | None) -> Iterator[None]:

    A context manager that chooses the ``lock_timeout`` index operations use.

    By default, the operations that create or drop an index concurrently set
    ``lock_timeout`` to 0 while they run and restore it afterwards. This lets a
    slow index build wait for older transactions to finish instead of failing.
    These include ``SaferAddIndexConcurrently``,
    ``SaferRemoveIndexConcurrently``, ``SaferAddUniqueConstraint`` and
    ``SaferAddFieldForeignKey``.

    Inside this context they use ``lock_timeout`` instead:

    - ``None``: keep the session's ``lock_timeout`` and don't change it.
    - A positive ``timedelta``: set ``lock_timeout`` to it while they run, then
      restore the session's value.
    - ``timedelta(0)``: disable the lock timeout, as they do by default.

    An index build that waits for longer than its ``lock_timeout`` fails with
    a lock timeout error, and the caller can retry it. If the build fails
    after the index's catalog entry has been created, an INVALID index is left
    behind. The operation drops it before trying again.

    Use this when a migration runner prefers to retry a blocked index build
    rather than wait behind long-running transactions, for example during a
    deploy. Code outside the context keeps the default behaviour.

    The value is stored in a context variable, so it only applies to code
    running in the current thread or task.

    :param lock_timeout: The ``lock_timeout`` for index operations, or
        ``None`` to keep the session's value.
    :type lock_timeout: datetime.timedelta | None
    :raise operations.IndexLockTimeoutWasNegative: When ``lock_timeout`` is
        negative.

    Example:

    .. code-block:: python

        import datetime

        from django.core.management import call_command

        from django_pg_migration_tools import operations, timeouts

        # Keep the 10s lock_timeout set below for index operations too.
        with operations.index_lock_timeout(None):
            with timeouts.apply_timeouts(
                using="default",
                lock_timeout=datetime.timedelta(seconds=10),
            ):
                call_command("migrate")

        # Give index operations their own 30s lock_timeout.
        with operations.index_lock_timeout(datetime.timedelta(seconds=30)):
            call_command("migrate")
