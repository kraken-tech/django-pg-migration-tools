Django Postgres Migration Tools
===============================

.. image:: https://img.shields.io/pypi/v/django-pg-migration-tools.svg
    :target: https://pypi.org/project/django-pg-migration-tools

.. image:: https://img.shields.io/pypi/pyversions/django-pg-migration-tools.svg
    :alt: Python versions
    :target: https://pypi.org/project/django-pg-migration-tools/

Django Postgres Migration Tools provides extra functionalities to make Django
migrations safer and more scalable.

Installation
============

Install from pypi::

    python -m pip install django-pg-migration-tools

Background
==========

In the past, traditional applications would do schema changes during scheduled
"maintenance windows." During this time, the application would stop all
traffic, and developers could make changes to the database or servers without
worrying about users.

In the context of modern systems requiring 24/7 availability, this is no longer
possible.

Django's migration system usually picks the easiest way to make a schema
change, but that isn't always safe. In fact, some operations Django does can
cause problems, like locking tables for too long, which can cause outages.

.. dropdown:: Meaning of **"safe"** in this context.
    :color: info
    :icon: info

    "Safe" means that the migration operation can run during normal application
    operating hours without interrupting the availability of the database or
    causing errors while using the database.

Modern applications also tend to make schema changes before deploying new code.
For example, in a blue/green deployment, a schema change is made to the
database, and then traffic is slowly moved from old servers (blue) to new
servers (green).

Once all traffic is migrated to the "green" servers, the "blue" servers usually
remain ready in case the application needs to be rolled back.

But here's the issue: if a Python/Django app has just renamed a model, the blue
servers still have the old model name saved in their app state. Only the green
servers have the updated name. This means any queries from the blue servers
will fail because they’re using the old name.

Similarly, if you need to roll back the migration, the green servers will have
the wrong code, and any queries they run will fail too.

This collection of guides and custom migration operation classes will show you
how to do schema changes safely and without downtime in modern apps using
Django.

.. toctree::
   :maxdepth: 3
   :caption: Contents

   usage/indexes
   usage/management_commands
   usage/operations
   usage/timeouts
