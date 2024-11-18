import datetime
import io
from typing import Any
from unittest import mock

import pytest
from django.core import management
from django.core.management import base
from django.core.management.commands.migrate import Command as DjangoMigrationMC
from django.db import connection
from django.test import utils

from django_pg_migration_tools import timeouts
from django_pg_migration_tools.management.commands import migrate_with_timeouts


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
    def test_when_stdout_is_passed_in(self, mock_handle):
        stdout = io.StringIO()

        def _mock_handle_side_effect(*args: Any, **kwargs: Any) -> None:
            kwargs["stdout"].write("hello world")

        mock_handle.side_effect = _mock_handle_side_effect

        management.call_command(
            "migrate_with_timeouts",
            lock_timeout_in_ms=50_000,
            statement_timeout_in_ms=100_000,
            stdout=stdout,
        )

        assert stdout.getvalue() == "hello world"

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
        timeout_mc = migrate_with_timeouts.Command()
        timeout_mc.add_arguments(timeout_mc_parser)
        timeout_mc_args = [action.dest for action in timeout_mc_parser._actions]

        # All the arguments are available, except that the timeout mc has 11
        # extra arguments to control timeouts and the retry mechanism.
        assert len(django_mc_args) == (len(timeout_mc_args) - 7)
        timeout_mc_args.remove("retry_callback_path")
        timeout_mc_args.remove("lock_timeout_in_ms")
        timeout_mc_args.remove("statement_timeout_in_ms")
        timeout_mc_args.remove("lock_timeout_max_retries")
        timeout_mc_args.remove("lock_timeout_retry_exp")
        timeout_mc_args.remove("lock_timeout_retry_max_wait_in_ms")
        timeout_mc_args.remove("lock_timeout_retry_min_wait_in_ms")
        assert django_mc_args == timeout_mc_args

    def test_missing_timeouts(self):
        with pytest.raises(ValueError, match="At least one of"):
            management.call_command("migrate_with_timeouts")

    def test_invalid_lock_retry_wait(self):
        with pytest.raises(
            ValueError, match="The minimum wait cannot be greater than the maximum"
        ):
            management.call_command(
                "migrate_with_timeouts",
                lock_timeout_in_ms=50_000,
                # min > max!
                lock_timeout_retry_min_wait_in_ms=10_000,
                lock_timeout_retry_max_wait_in_ms=5_000,
            )

    @pytest.mark.parametrize(
        "attr,value",
        [
            ("lock_timeout_max_retries", -42_000),
            ("lock_timeout_retry_exp", -50_000),
            ("lock_timeout_retry_max_wait_in_ms", -10_000),
            ("lock_timeout_retry_min_wait_in_ms", -40_000),
        ],
    )
    def test_forbidden_negative_value(self, attr, value):
        negative_attr = {attr: value}
        with pytest.raises(ValueError, match="is not a positive integer."):
            management.call_command(
                "migrate_with_timeouts",
                lock_timeout_in_ms=50_000,
                **negative_attr,
            )

    def test_invalid_callback_path(self):
        with pytest.raises(ModuleNotFoundError, match="No module named 'this.path'"):
            management.call_command(
                "migrate_with_timeouts",
                lock_timeout_in_ms=50_000,
                retry_callback_path="this.path.does.not.exist",
            )

    @pytest.mark.django_db(transaction=True)
    @mock.patch("time.sleep", autospec=True)
    @mock.patch("django.core.management.commands.migrate.Command.handle", autospec=True)
    def test_lock_timeout_retries_failed(self, mock_handle, mock_sleep):
        mock_handle.side_effect = [
            timeouts.DBLockTimeoutError("Bang!"),
            timeouts.DBLockTimeoutError("Bang!"),
            timeouts.DBLockTimeoutError("Bang!"),
            timeouts.DBLockTimeoutError("Bang!"),
        ]
        with pytest.raises(
            migrate_with_timeouts.MaximumRetriesReached,
            match=(
                "There were 4 lock timeouts. This happened because "
                "--lock-timeout-max-retries was set to 3."
            ),
        ):
            management.call_command(
                "migrate_with_timeouts",
                lock_timeout_in_ms=50_000,
                lock_timeout_max_retries=3,
            )

    @pytest.mark.django_db(transaction=True)
    @mock.patch("time.sleep", autospec=True)
    @mock.patch("time.time", autospec=True)
    @mock.patch("django.core.management.commands.migrate.Command.handle", autospec=True)
    def test_retry_callback_is_called(self, mock_handle, mock_time, mock_sleep):
        migration_stdout = """
        Operations to perform:
          Apply all migrations: ...
        Running migrations:
          Applying foo.0042_foo... OK
          Applying bar.0777_bar...
        """
        # Pretend 100 seconds have passed since the migration start and before
        # the callback was called.
        mock_time.side_effect = [0.0, 100.0]

        def _handle_mock_side_effect(*args: Any, **kwargs: Any) -> None:
            kwargs["stdout"].write(migration_stdout)
            raise timeouts.DBLockTimeoutError("Bang!")

        mock_handle.side_effect = _handle_mock_side_effect

        # Prove the callback happened by raising the migration stdout and the
        # value of time_since_start in the error message.
        match = f"migration:{migration_stdout} time_since_start:100.0 database:default"
        with pytest.raises(Exception, match=match):
            management.call_command(
                "migrate_with_timeouts",
                lock_timeout_in_ms=50_000,
                lock_timeout_max_retries=2,
                retry_callback_path=f"{__name__}.example_callback",
            )


class TestMigrateRetryStrategy:
    @mock.patch(
        "django_pg_migration_tools.management.commands.migrate_with_timeouts.MigrateRetryStrategy.can_migrate",
        autospec=True,
    )
    @mock.patch("time.sleep", autospec=True)
    @pytest.mark.parametrize(
        "exp,min_wait,max_wait,current_attempt,expected_result",
        [
            pytest.param(
                2,
                datetime.timedelta(seconds=1),
                datetime.timedelta(seconds=20),
                2,
                4,  # 2**2
                id="Basic scenario with 2 exponential.",
            ),
            pytest.param(
                2,
                datetime.timedelta(seconds=40),
                datetime.timedelta(seconds=20),
                2,
                40,  # min value takes over (40 seconds).
                id="The min_wait is longer than the calculated exponential.",
            ),
            pytest.param(
                5,
                datetime.timedelta(seconds=1),
                datetime.timedelta(seconds=50),
                10,
                50,  # max value takes over (50 seconds).
                id="The max_wait is shorter than the calculated exponential.",
            ),
            pytest.param(
                5,
                datetime.timedelta(seconds=1),
                datetime.timedelta(seconds=42),
                123456789,
                42,  # max value takes over (42 seconds).
                id="Calculation overflows and max_wait is taken instead.",
            ),
        ],
    )
    def test_wait_function(
        self,
        mock_sleep,
        mock_can_migrate,
        exp,
        min_wait,
        max_wait,
        current_attempt,
        expected_result,
    ):
        mock_can_migrate.return_value = True

        retry_strategy = migrate_with_timeouts.MigrateRetryStrategy(
            timeout_options=migrate_with_timeouts.MigrationTimeoutOptions(
                lock_timeout=None,
                statement_timeout=None,
                retry_callback=None,
                lock_retry_options=migrate_with_timeouts.TimeoutRetryOptions(
                    max_retries=999,
                    exp=exp,
                    min_wait=min_wait,
                    max_wait=max_wait,
                ),
            )
        )
        retry_strategy.retries = current_attempt
        retry_strategy.wait()
        mock_sleep.assert_called_once_with(expected_result)


def example_callback(retry_state: migrate_with_timeouts.RetryState) -> None:
    raise Exception(
        f"migration:{retry_state.stdout.getvalue()} "
        f"time_since_start:{retry_state.time_since_start.total_seconds()} "
        f"database:{retry_state.database}"
    )
