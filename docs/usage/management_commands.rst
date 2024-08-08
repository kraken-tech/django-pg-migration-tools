Management Commands
===================

migrate_with_timeouts
---------------------

This command wraps the :ref:`apply_timeouts() <apply_timeouts>` around
Django's ``migrate`` command, exposing two extra arguments:

- ``--lock-timeout-in-ms``
- ``--statement-timeout-in-ms``

 .. important::

   Both arguments are optional, but at least one must be provided!

These arguments will set the value of Postgres' ``lock_timeout`` and
``statement_timeout`` for the duration of the migration.

====================
Example: Basic usage
====================

Firstly, make sure to include ``django_pg_migration_tools`` in your
``INSTALLED_APPS``.

.. code-block:: python

    INSTALLED_APPS = [
        ...
        "django_pg_migration_tools",
        ...
    ]

Now you are all set to run the management command:

.. code-block:: bash

  ./manage.py migrate_with_timeouts --lock-timeout-in-ms=10000 --statement-timeout-in-ms=60000
