Management Commands
===================

``migrate_with_timeouts``
-------------------------

Runs database migrations with timeouts.

This command wraps the :ref:`apply_timeouts() <apply_timeouts>` around
Django's ``migrate`` command, exposing extra arguments to handle retries with
backoff propagation.

+++++++++++++++++++++++
lock_timeout arguments:
+++++++++++++++++++++++

* ``--lock-timeout-in-ms``: Sets the lock_timeout value for the migration.
* ``--lock-timeout-max-retries``: How many times to retry after getting a
  lock_timeout. Defaults to zero, which means no retries.
* ``--lock-timeout-retry-exp``: The exponential to use for exponential backoff
  retry if retries are enabled. Defaults to 2.
* ``--lock-timeout-retry-min-wait-in-ms``: minimum amount of time to wait
  between lock_timeout retries in milliseconds. Defaults to 3s.
* ``--lock-timeout-retry-max-wait-in-ms``: Maximum amount of time to wait
  between lock_timeout retries in milliseconds. Defaults to 10s.

++++++++++++++++++++++++++++
statement_timeout arguments:
++++++++++++++++++++++++++++

* ``--statement-timeout-in-ms``: Sets statement_timeout for the migration.

Note: Retry configurations are not available for statement timeouts at this
stage. If you have a use case where statement retries would be useful please
open an issue for discussion.

+++++++++++++++
retry callback:
+++++++++++++++

* ``--retry-callback-path``: Sets a callback to be called after a timeout
  event has happened. An example is set below:

  .. code-block:: python

    import logging

    from django_pg_migration_tools import timeouts
    from django_pg_migration_tools.management.commands import migrate_with_timeouts


    def timeout_callback(state: migrate_with_timeouts.RetryState) -> None:
        logging.info("A lock timeout just happened!")
        logging.info(f"{state.lock_timeouts_count} lock timeouts so far!")

++++++++++
How to use
++++++++++

1. Include ``django_pg_migration_tools`` in your ``INSTALLED_APPS``.

.. code-block:: python

    INSTALLED_APPS = [
        ...
        "django_pg_migration_tools",
        ...
    ]

2. Run the management command:

.. code-block:: bash

  ./manage.py migrate_with_timeouts \
    --lock-timeout-in-ms=10000 \
    --lock-timeout-max-retries=3 \
    --lock-timeout-retry-exp=2 \
    --lock-timeout-retry-min-wait-in-ms=3000 \
    --lock-timeout-retry-max-wait-in-ms=10000 \
    --statement-timeout-in-ms=10000 \
    --retry-callback-path="dotted.path.to.callback.function"
