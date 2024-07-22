import datetime
from unittest import mock

import pytest
from django.db import utils

from django_pg_migration_tools import timeouts


class TestApplyTimeouts:
    @pytest.mark.parametrize(
        "lock_timeout, statement_timeout",
        [
            (datetime.timedelta(seconds=1), datetime.timedelta(seconds=1)),
            (datetime.timedelta(seconds=2), datetime.timedelta(seconds=1)),
        ],
    )
    def test_raises_when_lock_timeout_equal_or_greater_than_statement_timeout(
        self,
        lock_timeout: datetime.timedelta | None,
        statement_timeout: datetime.timedelta | None,
    ) -> None:
        with pytest.raises(timeouts.RedundantLockTimeout):
            with timeouts.apply_timeouts(
                using="default",
                lock_timeout=lock_timeout,
                statement_timeout=statement_timeout,
            ):
                pass

    def test_raises_when_timeouts_not_provided(self) -> None:
        with pytest.raises(timeouts.TimeoutNotProvided):
            with timeouts.apply_timeouts(
                using="default",
                lock_timeout=None,
                statement_timeout=None,
            ):
                pass

    @pytest.mark.parametrize(
        "lock_timeout, statement_timeout",
        [
            (datetime.timedelta(seconds=0), None),
            (None, datetime.timedelta(seconds=0)),
            (datetime.timedelta(seconds=0), datetime.timedelta(seconds=0)),
            (datetime.timedelta(seconds=-1), None),
            (None, datetime.timedelta(seconds=-1)),
            (datetime.timedelta(seconds=-1), datetime.timedelta(seconds=-1)),
        ],
    )
    def test_raises_when_zero_timeout_provided(
        self,
        lock_timeout: datetime.timedelta | None,
        statement_timeout: datetime.timedelta | None,
    ) -> None:
        with pytest.raises(timeouts.TimeoutWasNotPositive):
            with timeouts.apply_timeouts(
                using="default",
                lock_timeout=lock_timeout,
                statement_timeout=statement_timeout,
            ):
                pass

    @mock.patch("django_pg_migration_tools.timeouts.connections", mock.MagicMock())
    @mock.patch(
        "django_pg_migration_tools.timeouts._in_transaction",
        mock.Mock(return_value=True),
    )
    def test_lock_timeout_error(self):
        with pytest.raises(timeouts.DBLockTimeoutError):
            with timeouts.apply_timeouts(
                using="default",
                lock_timeout=datetime.timedelta(seconds=1),
            ):
                raise utils.OperationalError("canceling statement due to lock timeout")

    @mock.patch("django_pg_migration_tools.timeouts.connections", mock.MagicMock())
    @mock.patch(
        "django_pg_migration_tools.timeouts._in_transaction",
        mock.Mock(return_value=True),
    )
    def test_statement_timeout_error(self):
        with pytest.raises(timeouts.DBStatementTimeoutError):
            with timeouts.apply_timeouts(
                using="default",
                statement_timeout=datetime.timedelta(seconds=2),
            ):
                raise utils.OperationalError(
                    "canceling statement due to statement timeout"
                )

    @mock.patch("django_pg_migration_tools.timeouts.connections", mock.MagicMock())
    @mock.patch(
        "django_pg_migration_tools.timeouts._in_transaction",
        mock.Mock(return_value=True),
    )
    def test_other_operational_error(self):
        """
        Verify that if the OperationalError isn't related to timeouts, we just
        re-raise the original exception.
        """
        with pytest.raises(utils.OperationalError):
            with timeouts.apply_timeouts(
                using="default",
                lock_timeout=datetime.timedelta(seconds=1),
                statement_timeout=datetime.timedelta(seconds=2),
            ):
                raise utils.OperationalError("some other error")

    @mock.patch("django_pg_migration_tools.timeouts.connections", mock.MagicMock())
    @mock.patch(
        "django_pg_migration_tools.timeouts._in_transaction",
        mock.Mock(return_value=True),
    )
    def test_close_transaction_leak_inside_transaction(self):
        with pytest.raises(timeouts.CloseTransactionLeakInsideTransaction):
            with timeouts.apply_timeouts(
                using="default",
                lock_timeout=datetime.timedelta(seconds=1),
                close_transaction_leak=True,
            ):
                pass
