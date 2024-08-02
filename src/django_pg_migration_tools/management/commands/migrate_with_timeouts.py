import datetime
from typing import Any

from django.core.management import base
from django.core.management.commands.migrate import Command as DjangoMigrationMC

from django_pg_migration_tools import timeouts


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
        super().add_arguments(parser)

    @base.no_translations
    def handle(self, *args: Any, **options: Any) -> None:
        statement_timeout: None | int = options["statement_timeout_in_ms"]
        lock_timeout: None | int = options["lock_timeout_in_ms"]

        if statement_timeout is None and lock_timeout is None:
            raise ValueError(
                "At least one of --lock-timeout-in-ms or --statement-timeout-in-ms "
                "must be specified."
            )

        if statement_timeout is not None:
            statement_timeout = datetime.timedelta(
                seconds=int(statement_timeout / 1_000)
            )
        if lock_timeout is not None:
            lock_timeout = datetime.timedelta(seconds=int(lock_timeout / 1_000))

        with timeouts.apply_timeouts(
            using=options["database"],
            lock_timeout=lock_timeout,
            statement_timeout=statement_timeout,
        ):
            super().handle(*args, **options)
