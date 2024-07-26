Timeouts
========

The ``timeouts`` facility provides users with the ability to set Postgres
`lock_timeout
<http://web.archive.org/web/20240607131902/https://www.postgresql.org/docs/16/runtime-config-client.html#GUC-LOCK-TIMEOUT>`_
and `statement_timeout
<http://web.archive.org/web/20240607131902/https://www.postgresql.org/docs/16/runtime-config-client.html#GUC-STATEMENT-TIMEOUT>`_
values.

.. note::

  **Why use database timeouts?**

  Queries that change the database schema can get blocked because of
  long-running transactions and queries. These blocked migration queries, in
  turn, block subsequent queries as well. This is the basic ingredient for an
  outage.

  Using responsible values of ``lock_timeout`` and ``statement_timeout`` in
  places where you might have a long-running transaction or query will help
  preventing this type of situation in the first place.

Function Definitions
--------------------

.. py:function:: timeouts.apply_timeouts(using: str, lock_timeout: datetime.timedelta | None = None, statement_timeout: datetime.timedelta | None = None, close_transaction_leak: bool = False) -> Iterator[None]:

    A context manager to set Postgres timeouts.

    Effectively executes the following SQL statements if in a transaction:

      ``SET LOCAL lock_timeout '<lock_timeout>';``

      ``SET LOCAL statement_timeout '<lock_timeout>';``

    If not in a transaction, executes the following instead:

      ``SET SESSION lock_timeout '<lock_timeout>';``

      ``SET SESSION statement_timeout '<lock_timeout>';``

   :param using: Mandatory "using" database alias to use.
   :type using: str
   :param lock_timeout: Optional value to set "lock_timeout".
   :type lock_timeout: datetime.timedelta | None
   :param statement_timeout: Optional value to set "statement_timeout".
   :type statement_timeout: datetime.timedelta | None
   :param close_transaction_leak: Whether to close a leaky aborted transaction automatically.
   :type close_transaction_leak: bool = False
   :raise timeouts.TimeoutNotProvided: If neither lock nor statement timeouts are provided.
   :raise timeouts.TimeoutWasNotPositive: If either value of lock or statement timeout is negative.
   :raise timeouts.RedundantLockTimeout: When lock and statement timeouts are set to the same value. This is redundant because statement timeouts trump lock timeouts.
   :raise timeouts.CloseTransactionLeakInsideTransaction: When close_transaction_leak is True and running inside a transaction.
   :raise timeouts.DBLockTimeoutError: When the value of lock_timeout is reached during runtime.
   :raise timeouts.DBStatementTimeoutError: When the value of statement_timeout is reached during runtime.
   :raise timeouts.UnsupportedTimeoutBehaviour: Sentinel that is raised when a particular behaviour isn't supported.
   :return: yields the result.
   :rtype: Iterator[None]

Example Usage
-------------

Here are some examples of how to use the ``apply_sync`` function.

======================
Example 1: Basic Usage
======================

.. code-block:: python

   import datetime
   from django_pg_migration_tools import timeouts

   with timeouts.apply_timeouts(
       using="default",
       lock_timeout=datetime.timedelta(seconds=10),
   ):
       # Code in this block will have a lock timeout of 10s.
       ...
       with timeouts.apply_timeouts(
           using="default",
           lock_timeout=datetime.timedelta(seconds=5),
       ):
           # Code inside this block will have a lock timeout of 5s
           ...

       # We are back to the parent block, so lock timeout is 10s again.
       ...

=====================================================
Example 2: Invalid Parameters: Missing timeout values
=====================================================

.. code-block:: python

   import datetime
   from django_pg_migration_tools import timeouts

   with timeouts.apply_timeouts(
       using="default",
   ):
     pass

**Output:**

.. code-block:: text

   django_pg_migration_tools.timeouts.TimeoutNotProvided: Caller must set at least one of `lock_timeout` or `statement_timeout`.


=============================================================
Example 3: Invalid Parameters: Negative timeout not permitted
=============================================================

.. code-block:: python

   import datetime
   from django_pg_migration_tools import timeouts

   with timeouts.apply_timeouts(
       using="default",
       lock_timeout=datetime.timedelta(seconds=-5),
       # Either lock_timeout or statement_timeout negative.
       # The following would've raised an error as well.
       # statement_timeout=datetime.timedelta(seconds=-5),
   ):
     pass

**Output:**

.. code-block:: text

   django_pg_migration_tools.timeouts.TimeoutWasNotPositive: Timeouts must be greater than zero.
