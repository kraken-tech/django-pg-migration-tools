# Django Postgres Migration Tools

Tools to make Django migrations safer and more scalable.

## Documentation

[Full documentation](https://django-pg-migration-tools.readthedocs.io/en/latest/).

## Main Features

- **Safer migration operations for**:
  - [Adding unique constraints](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/operations.html#SaferAddUniqueConstraint)
  - [Removing unique constraints](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/operations.html#SaferRemoveUniqueConstraint)
  - [Adding check constraints](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/operations.html#SaferAddCheckConstraint)
  - [Removing check constraints](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/operations.html#SaferRemoveCheckConstraint)
  - [Adding indexes (concurrently)](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/operations.html#SaferAddIndexConcurrently)
  - [Removing indexes (concurrently)](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/operations.html#SaferRemoveIndexConcurrently)
  - [Setting a column to NOT NULL](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/operations.html#SaferAlterFieldSetNotNull)
  - [Adding foreign keys](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/operations.html#SaferAddFieldForeignKey)
  - [Removing foreign keys](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/operations.html#SaferRemoveFieldForeignKey)
  - [Adding one to one fields](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/operations.html#SaferAddFieldOneToOne)

- **Database timeouts**:
  - A context manager to apply `statement_timeout` and/or `lock_timeout` to
    either a transaction or a Postgres session. See [apply_timeouts](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/timeouts.html#timeouts.apply_timeouts)

- **Management commands**:
  - Run migrations with `statement_timeout` and `lock_timeout` by using
    [migrate_with_timeouts](https://django-pg-migration-tools.readthedocs.io/en/latest/usage/management_commands.html#migrate-with-timeouts)
