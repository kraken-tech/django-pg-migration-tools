Timeouts
========

Provides the ability to set Postgres
`lock_timeout
<http://web.archive.org/web/20240607131902/https://www.postgresql.org/docs/16/runtime-config-client.html#GUC-LOCK-TIMEOUT>`_
and `statement_timeout
<http://web.archive.org/web/20240607131902/https://www.postgresql.org/docs/16/runtime-config-client.html#GUC-STATEMENT-TIMEOUT>`_
values.

.. note::

  **Why use database timeouts?**

  Timeouts are a way of preventing queries / transactions running for too long.

  Why are long-running queries a problem? It's because they can block queries that change the database schema.
  These blocked migration queries, in turn, block subsequent queries as well, potentially causing an outage.

Function Definitions
--------------------
.. _apply_timeouts:

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

Example
-------

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
           # Code inside this block will have a lock timeout of 5s.
           ...

       # We are back to the parent block, so lock timeout is 10s again.
       ...
