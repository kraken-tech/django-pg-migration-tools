# Changelog and Versioning

All notable changes to this project will be documented in this file.

The format is based on [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Fixes a bug in `migrate_with_timeouts` where passing a `stdout` parameter to
  the command would raise a TypeError.
  passed in.

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
