import datetime
from unittest import mock

import pytest

from django_pg_migration_tools import timeout_retries


class TestMigrateRetryStrategy:
    @mock.patch(
        "django_pg_migration_tools.timeout_retries.MigrateRetryStrategy.can_migrate",
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

        retry_strategy = timeout_retries.MigrateRetryStrategy(
            timeout_options=timeout_retries.MigrationTimeoutOptions(
                lock_timeout=None,
                statement_timeout=None,
                retry_callback=None,
                lock_retry_options=timeout_retries.TimeoutRetryOptions(
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
