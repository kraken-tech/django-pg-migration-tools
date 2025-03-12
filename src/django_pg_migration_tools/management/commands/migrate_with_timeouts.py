import hashlib
import io
import time
from typing import Any

from django.core.management import base
from django.core.management.commands.migrate import Command as DjangoMigrationMC
from django.db import connections

from django_pg_migration_tools import timeout_retries, timeouts


class MaximumRetriesReached(base.CommandError):
    pass


class Command(DjangoMigrationMC):
    help = (
        "Wrapper around Django's migrate command that sets a lock_timeout "
        "value to ensure migrations don't wait for locks for too long."
    )

    def add_arguments(self, parser: base.CommandParser) -> None:
        parser.add_argument(
            "--lock-timeout-in-ms",
            dest="lock_timeout_in_ms",
            type=int,
            required=False,
            help="Value to set as lock_timeout in milliseconds.",
        )
        parser.add_argument(
            "--statement-timeout-in-ms",
            dest="statement_timeout_in_ms",
            type=int,
            required=False,
            help="Value to set as statement_timeout in milliseconds.",
        )
        parser.add_argument(
            "--retry-callback-path",
            dest="retry_callback_path",
            type=str,
            required=False,
            help=(
                "If retries are set, this argument can be used with the dotted path "
                "to a function to be called between retries. The function must "
                "follow this signature: f(retry_state) where `retry_state` "
                "is the dataclass: "
                "django_pg_migration_tools.management.commands.migrate_with_timeouts.RetryState."
                "This callback can be useful for calling loggers after each "
                "retry."
            ),
        )
        parser.add_argument(
            "--lock-timeout-max-retries",
            dest="lock_timeout_max_retries",
            type=int,
            required=False,
            default=0,
            help=(
                "How many times to retry after a lock timeout happened. "
                "Defaults to zero, which means no retries - the migration "
                "fails immediately upon lock timeout."
            ),
        )
        parser.add_argument(
            "--lock-timeout-retry-exp",
            dest="lock_timeout_retry_exp",
            type=int,
            required=False,
            default=2,
            help="The value for the exponent for retry backoff delay. Defaults to 2.",
        )
        parser.add_argument(
            "--lock-timeout-retry-max-wait-in-ms",
            dest="lock_timeout_retry_max_wait_in_ms",
            type=int,
            required=False,
            default=10_000,
            help=(
                "Sets a limit to the maximum length of time between subsequent "
                "exponential backoff retries. Defaults to 10s."
            ),
        )
        parser.add_argument(
            "--lock-timeout-retry-min-wait-in-ms",
            dest="lock_timeout_retry_min_wait_in_ms",
            type=int,
            required=False,
            default=3_000,
            help=(
                "Sets a limit to the minimum length of time between subsequent "
                "exponential backoff retries. Defaults to 3s."
            ),
        )
        super().add_arguments(parser)

    @base.no_translations
    def handle(self, *args: Any, **options: Any) -> None:
        timeout_options = timeout_retries.MigrationTimeoutOptions.from_dictionary(
            options
        )
        timeout_options.validate()
        retry_strategy = timeout_retries.MigrateRetryStrategy(
            timeout_options=timeout_options
        )

        stdout: io.StringIO = options.pop("stdout", io.StringIO())
        start_time: float = time.time()
        database: str = options["database"]
        Locking().acquire_advisory_session_lock(
            using=database, value="migrate-with-timeouts"
        )
        while retry_strategy.can_migrate():
            try:
                with timeouts.apply_timeouts(
                    using=database,
                    lock_timeout=timeout_options.lock_timeout,
                    statement_timeout=timeout_options.statement_timeout,
                ):
                    super().handle(*args, stdout=stdout, **options)
                    return
            except timeouts.DBLockTimeoutError as exc:
                retry_strategy.increment_retry_count()
                retry_strategy.wait()
                retry_strategy.attempt_callback(exc, stdout, start_time, database)

        raise MaximumRetriesReached(
            f"Please consider trying a longer retry configuration or "
            f"investigate whether there were long-running transactions "
            f"during the migration. "
            f"There were {retry_strategy.retries} lock timeouts. "
            f"This happened because --lock-timeout-max-retries was set to "
            f"{timeout_options.lock_retry_options.max_retries}."
        )


class LockAlreadyAcquired(Exception):
    pass


class Locking:
    def acquire_advisory_session_lock(self, using: str, value: str) -> None:
        with connections[using].cursor() as cursor:
            lock_id = self._cast_lock_value_to_int(value)
            cursor.execute(f"SELECT pg_try_advisory_lock({lock_id});")
            acquired = cursor.fetchone()[0]

        if not acquired:
            raise LockAlreadyAcquired(
                "Another migrate_with_timeouts command is already running."
            )

    def _cast_lock_value_to_int(self, value: str) -> int:
        """
        Based on:
        https://github.com/Opus10/django-pglock/blob/bf7422d3a74eed8196e13f6b28b72fb0623560e5/pglock/core.py#L137-L139
        """
        return int.from_bytes(
            hashlib.sha256(value.encode("utf-8")).digest()[:8], "little", signed=True
        )
