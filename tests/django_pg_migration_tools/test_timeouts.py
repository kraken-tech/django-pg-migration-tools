import datetime
from unittest import mock

import pytest
from django.db import connection, connections, transaction, utils
from django.test import utils as test_utils

from django_pg_migration_tools import timeouts


class _Leaky:
    """
    A context manager that leaks a transaction when it does not exit
    successfully.

    This simulates a corner-case scenario where a leaky transaction falls
    through to the `finally` block of the `db.timeouts` context manager.

    This leaky transaction may be in a state of "ABORTED", which means that
    all subsequent cursor.execute calls will fail.

    This situation happens during a Django migration, as the
    BaseDatabaseSchemaEditor class is leaky-prone.

    Refer to the code:
        - https://github.com/django/django/blob/6f7c0a4d66f36c59ae9eafa168b455e462d81901/django/db/backends/base/schema.py#L156-L168

    On line 166, if the command `self.execute` fails, the atomic.__exit__
    block is never called thus producing the leak.
    """

    def __enter__(self):
        self.atomic = transaction.atomic("default")
        self.atomic.__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        with connection.cursor() as cursor:
            # This will raise an exception that aborts the transaction
            # because the IntModel model has `int_field` as required field
            # and we aren't giving it.
            cursor.execute("INSERT INTO example_app_intmodel (id) VALUES (%s)", [1])
        # Note that the code below is never reached, so the atomic
        # block is never exited, just like what the Django migrator
        # does!
        self.atomic.__exit__(exc_type, exc_value, traceback)


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

    @pytest.mark.django_db
    @mock.patch(
        "django_pg_migration_tools.timeouts._in_transaction",
        mock.Mock(return_value=True),
    )
    def test_happy_path_when_timeout_is_not_raised(self):
        with test_utils.CaptureQueriesContext(connections["default"]) as queries:
            with timeouts.apply_timeouts(
                using="default",
                lock_timeout=datetime.timedelta(seconds=1),
            ):
                pass

        assert queries[0]["sql"] == "SHOW lock_timeout"
        assert queries[1]["sql"] == "SET LOCAL lock_timeout = '1000ms'"
        assert queries[2]["sql"] == "SET LOCAL lock_timeout = '0'"

    # We need control of transactions for this test otherwise we won't be able
    # to test the SESSION lock_level for the leaky behaviour, which requires us
    # to be out of a transaction.
    @pytest.mark.django_db(transaction=True)
    def test_leave_transaction_leak_open(self):
        with pytest.raises(timeouts.UnsupportedTimeoutBehaviour):
            with timeouts.apply_timeouts(
                using="default",
                lock_timeout=datetime.timedelta(seconds=44),
                # We aren't allowing the utility to close the transaction leak
                # and therefore we raise an unsupported error because we are
                # inside an aborted transaction.
                close_transaction_leak=False,
            ):
                try:
                    with _Leaky():
                        pass
                except Exception:
                    # We handle the exception, so that the db.timeouts finally
                    # block can reach a state where it tries to revert from a
                    # leaky transaction.
                    pass

        # We need to manually close the transaction here so that pytest doesn't
        # blow up. Remember that we created the leak ourselves so we need to
        # clean up...
        conn = transaction.get_connection()
        conn.close()
        # Open a clean new connection so that pytest can use it for teardown.
        conn.connect()

    # We need control of transactions for this test otherwise we won't be able
    # to test the SESSION lock_level for the leaky behaviour, which requires us
    # to be out of a transaction.
    @pytest.mark.django_db(transaction=True)
    def test_close_when_transaction_leak(self):
        with timeouts.apply_timeouts(
            using="default",
            lock_timeout=datetime.timedelta(seconds=44),
            # This test won't raise an exception because we are closing the
            # leaky transaction.
            close_transaction_leak=True,
        ):
            try:
                with _Leaky():
                    pass
            except Exception:
                # We handle the exception, so that the db.timeouts finally
                # block can reach a state where it tries to revert from a leaky
                # transaction.
                pass
