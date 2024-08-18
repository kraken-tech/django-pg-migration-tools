Management Commands
===================

migrate_with_timeouts
---------------------

Runs database migrations with timeouts.

This command wraps the :ref:`apply_timeouts() <apply_timeouts>` around
Django's ``migrate`` command, exposing two extra arguments:

- ``--lock-timeout-in-ms``
- ``--statement-timeout-in-ms``

 .. important::

   Both arguments are optional, but at least one must be provided!

These arguments will set the value of Postgres' ``lock_timeout`` and
``statement_timeout`` for the duration of the migration.

==========
How to use
==========

1. Include ``django_pg_migration_tools`` in your ``INSTALLED_APPS``.

.. code-block:: python

    INSTALLED_APPS = [
        ...
        "django_pg_migration_tools",
        ...
    ]

2. Run the management command:

.. code-block:: bash

  ./manage.py migrate_with_timeouts --lock-timeout-in-ms=10000 --statement-timeout-in-ms=60000
