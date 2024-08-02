from unittest import mock

import pytest
from django.core import management
from django.core.management import base
from django.core.management.commands.migrate import Command as DjangoMigrationMC
from django.db import connection
from django.test import utils

from django_pg_migration_tools.management.commands.migrate_with_timeouts import (
    Command as TimeoutMigrateMC,
)


class TestMigrateWithTimeoutsCommand:
    @mock.patch("django.core.management.commands.migrate.Command.handle", autospec=True)
    @pytest.mark.django_db(transaction=True)
    def test_both_timeouts_are_applied(self, mock_migrate_handle):
        with utils.CaptureQueriesContext(connection) as queries:
            management.call_command(
                "migrate_with_timeouts",
                lock_timeout_in_ms=50_000,
                statement_timeout_in_ms=100_000,
            )

        assert queries[0]["sql"] == "SHOW lock_timeout"
        assert queries[1]["sql"] == "SET SESSION lock_timeout = '50000ms'"
        assert queries[2]["sql"] == "SHOW statement_timeout"
        assert queries[3]["sql"] == "SET SESSION statement_timeout = '100000ms'"
        assert queries[4]["sql"] == "SET SESSION lock_timeout = '0'"
        assert queries[5]["sql"] == "SET SESSION statement_timeout = '0'"
        assert len(queries) == 6

    @mock.patch("django.core.management.commands.migrate.Command.handle", autospec=True)
    @pytest.mark.django_db(transaction=True)
    def test_only_lock_timeout_is_applied(self, mock_migrate_handle):
        with utils.CaptureQueriesContext(connection) as queries:
            management.call_command(
                "migrate_with_timeouts",
                lock_timeout_in_ms=50_000,
            )

        assert queries[0]["sql"] == "SHOW lock_timeout"
        assert queries[1]["sql"] == "SET SESSION lock_timeout = '50000ms'"
        assert queries[2]["sql"] == "SET SESSION lock_timeout = '0'"
        assert len(queries) == 3

    @mock.patch("django.core.management.commands.migrate.Command.handle", autospec=True)
    @pytest.mark.django_db(transaction=True)
    def test_only_statement_timeout_is_applied(self, mock_migrate_handle):
        with utils.CaptureQueriesContext(connection) as queries:
            management.call_command(
                "migrate_with_timeouts",
                statement_timeout_in_ms=50_000,
            )

        assert queries[0]["sql"] == "SHOW statement_timeout"
        assert queries[1]["sql"] == "SET SESSION statement_timeout = '50000ms'"
        assert queries[2]["sql"] == "SET SESSION statement_timeout = '0'"
        assert len(queries) == 3

    def test_interface(self):
        """
        All inputs available to `migrate` should also be available to
        `migrate_with_timeouts`.
        """
        django_mc_parser = base.CommandParser()
        django_mc = DjangoMigrationMC()
        django_mc.add_arguments(django_mc_parser)
        django_mc_args = [action.dest for action in django_mc_parser._actions]

        timeout_mc_parser = base.CommandParser()
        timeout_mc = TimeoutMigrateMC()
        timeout_mc.add_arguments(timeout_mc_parser)
        timeout_mc_args = [action.dest for action in timeout_mc_parser._actions]

        # All the arguments are available, except that the timeout mc has two
        # extra arguments (--lock-timeout-in-ms, --statement-timeout-in-ms)
        assert len(django_mc_args) == (len(timeout_mc_args) - 2)
        timeout_mc_args.remove("lock_timeout_in_ms")
        timeout_mc_args.remove("statement_timeout_in_ms")
        assert django_mc_args == timeout_mc_args

    def test_missing_timeouts(self):
        with pytest.raises(ValueError, match="At least one of"):
            management.call_command("migrate_with_timeouts")
