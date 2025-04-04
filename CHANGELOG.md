# Changelog and Versioning

All notable changes to this project will be documented in this file.

The format is based on [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.19] - 2025-04-04

### Fixed

- `SaferAddFieldForeignKey` did not work correctly when the primary key field
  on the referred table had a column name that different from the field name.

## [0.1.18] - 2025-02-11

### Fixed

- `SaferAlterFieldSetNotNull` did not work when the field dropping the NOT NULL
  constraint was a ForeignKey. An error such as:
  `django.db.utils.ProgrammingError: column "foo" does not exist` would have
  been raised instead.

## [0.1.17] - 2025-01-14

### Added

- Enhanced `SaferAddUniqueConstraint` to support a `UniqueConstraint` with the
  `deferrable` argument.
- A new operation to remove a foreign key field: `SaferRemoveFieldForeignKey`.
- A new operation `SaferRemoveCheckConstraint` which does the opposite of
  `SaferAddCheckConstraint`.

## [0.1.16] - 2025-01-08

### Added

- A new type of index `UniqueIndex` that also accepts conditions.
- A new operation to add one-to-one fields in a safer way:
  `SaferAddFieldOneToOne`.

## [0.1.15] - 2024-12-19

### Changed

- The `migrate_with_timeouts` command now acquires a session-level advisory
  lock before it begins. This helps preventing concurrent instances of the
  migrate command from running at the same time, which could cause unexpected
  crashes.

## [0.1.14] - 2024-12-17

### Added

- A new operation to add a check constraint to an existing table has been
  added: `SaferAddCheckConstraint`.

### Fixed

- Introspection queries are now skipped when running `sqlmigrate`. The result
  of the `sqlmigrate` command will instead show a best-effort plan to perform
  the migration without knowing the current state of the database. When running
  `migrate`, however, the introspection queries will find the most adequate
  plan; taking into consideration idempotency and reentrancy.

## [0.1.13] - 2024-12-11

### Fixed

- Use `schema_editor` instead of opening a new cursor for executing DDL
  queries. This is so that `sqlmigrate` can show the queries without actually
  running them.

## [0.1.12] - 2024-12-10

### Fixed

- Fixed a bug preventing the `SaferAddFieldForeignKey` operation of being
  initialised with a `to` argument that is a string instead of models.Model

## [0.1.11] - 2024-12-06

### Added

- A new operation to add foreign key fields to an existing table has been
  added: `SaferAddFieldForeignKey`

## [0.1.10] - 2024-11-22

### Updated

- The `migrate_with_timeouts` callback argument `RetryState` now includes the
  name of the database that the retry being performed is associated with.

### Fixed

- Fixed an import error introduced on v0.1.9 that would break that version for
  users of psycopg2.

## [0.1.9] - 2024-11-14

### Added

- The `SaferAlterFieldSetNotNull` operation was added. This will more safely
  change a field from nullable to not nullable.

## [0.1.8] - 2024-10-31

### Fixed

- Fixed a bug where `SaferAddUniqueConstraint` and
  `SaferRemoveUniqueConstraint` would accept unique constraints with conditions
  on them, but produce invalid SQL. Unique constraints with conditions are now
  handled as partial unique indexes, as per the equivalent Django
  `AddConstraint` and `RemoveConstraint` operations.

### Added

- `SaferRemoveUniqueConstraint` operation was added. This is the complement for
  `SaferAddUniqueConstraint` - but with the forward and backwards operations
  swapped.

## [0.1.7] - 2024-10-09

### Fixed

- Fixes a bug in `migrate_with_timeouts` where passing a `stdout` parameter to
  the command would raise a TypeError.

## [0.1.6] - 2024-10-08

### Changed

- `migrate_with_timeouts` callbacks can now access the migration stdout via
  `RetryState.stdout` and the time since the migration started with
  `RetryState.time_since_start`.
- Added a new operation `SaferAddUniqueConstraint` that provides a way to
  safely create unique constraints.

## [0.1.5] - 2024-10-07

### Changed

- `migrate_with_timeouts` will now raise `MaximumRetriesReached` instead of
  `CommandError` when the maximum number of retries is reached.

## [0.1.4] - 2024-10-01

### Changed

- The implementation of `migrate_with_timeouts` now has additional flags to
  perform retries:
  ```bash
  ./manage.py migrate_with_timeouts \
    --lock-timeout-in-ms=10000 \
    --lock-timeout-max-retries=3 \
    --lock-timeout-retry-exp=2 \
    --lock-timeout-retry-min-wait-in-ms=3000 \
    --lock-timeout-retry-max-wait-in-ms=10000 \
    --statement-timeout-in-ms=10000 \
    --retry-callback-path="dotted.path.to.callback.function"
  ```

## [0.1.3] - 2024-09-03

### Added

- `SaferRemoveIndexConcurrently` migration operation to drop Postgres indexes
  in a safer way than Django's `RemoveIndexConcurrently`.

### Changed

- The internal implementation for `SaferAddIndexConcurrently` has been changed
  to inherit from Django's `AddIndexConcurrently` operation rather than
  Django's `Operation` class. This means that the interface is now the same and
  the "hints" argument is not valid any longer.

## [0.1.2] - 2024-08-23

- Fixes a bug where the `SaferAddIndexConcurrently` class would try to perform
  a migration regardless of whether the router would allow it (through
  `router.allow_migrate`).

## [0.1.1] - 2024-08-14

- Non-functional changes for the documentation to be properly linked in PyPI.

## [0.1.0] - 2024-08-12

### Added

- `apply_timeouts` context manager for controlling the values of lock_timeout
  and statement_timeout.
- `migrate_with_timeouts` management command that applies lock_timeout and
  statement_timeout to Django's migrate command.
- `SaferAddIndexConcurrently` migration operation to create new Postgres
  indexes in a safer, idempotent way.
