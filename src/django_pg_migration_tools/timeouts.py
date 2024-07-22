import contextlib
import datetime
from collections.abc import Iterator

from django.db import connections, transaction, utils


class TimeoutNotProvided(Exception):
    pass


class TimeoutWasNotPositive(Exception):
    pass


class RedundantLockTimeout(Exception):
    pass


class CloseTransactionLeakInsideTransaction(Exception):
    pass


class DBTimeoutError(utils.OperationalError):
    pass


class DBLockTimeoutError(DBTimeoutError):
    pass


class DBStatementTimeoutError(DBTimeoutError):
    pass


class UnsupportedTimeoutBehaviour(Exception):
    pass


@contextlib.contextmanager
def apply_timeouts(
    *,
    using: str,
    lock_timeout: datetime.timedelta | None = None,
    statement_timeout: datetime.timedelta | None = None,
    close_transaction_leak: bool = False,
) -> Iterator[None]:
    """
    A context manager to set Postgres timeouts.

    Effectively executes the following SQL statements if in a transaction:

        SET LOCAL lock_timeout '<lock_timeout>';
        SET LOCAL statement_timeout '<lock_timeout>';

    If not in a transaction, executes the following instead:

        SET SESSION lock_timeout '<lock_timeout>';
        SET SESSION statement_timeout '<lock_timeout>';

    Timeouts can be applied granularly. Different blocks of code executing SQL
    statements can be set with different timeouts.

    Upon exiting the context manager, the timeouts will be reset to what they
    were before the manager was called. This happens irrespective of whether
    the program erred or not.

    For example:

        from django_pg_migration_tools import timeouts

        with timeouts.apply_timeouts(
            using="default",
            lock_timeout=datetime.timedelta(seconds=10),
        ):
            # Code in this block will have a lock timeout of 10s.
            ...
            with timeouts.apply_timeouts(
                using="default",
                lock_timeout=datetime.timedelta(seconds=5),
            ):
                # Code inside this block will have a lock timeout of 5s
                ...

            # We are back to the parent block, so lock timeout is 10s again.
            ...

    Note: The `close_transaction_leak` argument is only necessary in very
    specific corner-case scenarios, and should *not* be changed to `True`
    unless:
        1. The code being wrapped by the ctx manager is _knowingly_ leaky.
        2. There are no potential side-effects from closing the leaked
           transactions.

    More detailed comments and explanations are found in the implementation
    below.
    """
    if lock_timeout is None and statement_timeout is None:
        raise TimeoutNotProvided(
            "Caller must set at least one of `lock_timeout` or `statement_timeout`."
        )

    lock_timeout_in_ms = (
        int(lock_timeout.total_seconds() * 1000) if lock_timeout is not None else None
    )
    statement_timeout_in_ms = (
        int(statement_timeout.total_seconds() * 1000)
        if statement_timeout is not None
        else None
    )

    if (lock_timeout_in_ms is not None and lock_timeout_in_ms <= 0) or (
        statement_timeout_in_ms is not None and statement_timeout_in_ms <= 0
    ):
        raise TimeoutWasNotPositive("Timeouts must be greater than zero.")

    if (lock_timeout_in_ms and statement_timeout_in_ms) and (
        lock_timeout_in_ms >= statement_timeout_in_ms
    ):
        raise RedundantLockTimeout(
            "It is pointless to set `lock_timeout` to a value equal or "
            "greater than `statement_timeout`. The latter will fail first."
        )

    if _in_transaction(using=using):
        lock_level = "LOCAL"
    else:
        lock_level = "SESSION"

    if lock_level == "LOCAL" and close_transaction_leak:
        raise CloseTransactionLeakInsideTransaction(
            # Note: there will still be an error, but it is not the job of
            # a timeouts ctx manager to fix nor handle it.
            "Closing a leaky transaction inside another transaction is not "
            "necessary. The database will automatically rollback the LOCAL "
            "timeouts once the code errors or the transaction rolls back."
        )

    # Store the previous timeouts before the context manager was called so that
    # when the context manager exits the values can be rolled back.
    previous_lock_timeout: None | str = None
    previous_statement_timeout: None | str = None

    with connections[using].cursor() as cursor:
        if lock_timeout_in_ms is not None:
            cursor.execute("SHOW lock_timeout")
            previous_lock_timeout = cursor.fetchone()[0]
            cursor.execute(
                f"SET {lock_level} lock_timeout = %s", [f"{lock_timeout_in_ms}ms"]
            )
        if statement_timeout_in_ms is not None:
            cursor.execute("SHOW statement_timeout")
            previous_statement_timeout = cursor.fetchone()[0]
            cursor.execute(
                f"SET {lock_level} statement_timeout = %s",
                [f"{statement_timeout_in_ms}ms"],
            )

    try:
        yield
        if lock_level == "LOCAL":
            # When the timeouts are set inside a transaction, we only need to
            # manually revert them when the wrapped code executes successfully.
            # Otherwise, the database would have rolled it back for us.
            _reset_timeouts(
                using=using,
                lock_level=lock_level,
                previous_lock_timeout=previous_lock_timeout,
                previous_statement_timeout=previous_statement_timeout,
            )
    except utils.OperationalError as exc:
        # Transform django.db OperationalError exception into two different
        # exceptions that can be handled either together via DBTimeoutError
        # inheritance, or granuarly via
        # DBLockTimeoutError/DBStatementTimeoutError for finer control.
        error_msg = str(exc)
        if "canceling statement due to lock timeout" in error_msg:
            raise DBLockTimeoutError from exc
        elif "canceling statement due to statement timeout" in error_msg:
            raise DBStatementTimeoutError from exc
        else:
            raise
    finally:
        if lock_level == "SESSION":
            conn = transaction.get_connection(using=using)
            if conn.in_atomic_block:
                # If this is a SESSION command, it means that the context
                # manager ran *outside* of a transaction.
                #
                # If we reached this point, and the code is now *inside* of a
                # transaction, as per the `if` conditional above, it means that
                # some external code is leaking a potentially aborted
                # transaction, and failed to roll it back.
                #
                # We cannot clean up the SESSION timeouts if we are now inside
                # a transaction, and the code below would raise exceptions if
                # attempting to do so, as the connection is marked as unusable.
                #
                # In some cases it might be helpful for it to blow up, so that
                # the developer knows they have encountered a leak.
                #
                # In other cases, such as running a Django migration with
                # timeouts, it is unhelpful to let the leak carry through,
                # because the `migrate` command will blow up, and if the
                # transaction is ABORTED, it means that the migration already
                # failed in any case.
                if close_transaction_leak and not conn.is_usable():
                    # This will ping the database to verify if the connection
                    # is usable or not.
                    #
                    # If this is an ABORTED transaction, and we are using the
                    # same connection, the result will be False.
                    #
                    # Django does not provide hooks where we can clean up the
                    # transaction directly. Instead, we just close the
                    # connection and open a new one - leaving the database to
                    # clean up the ABORTED transaction by itself.
                    conn.close_if_unusable_or_obsolete()
                    conn.connect()
                else:
                    # Unsupported behaviour.
                    # It's hard to leak a non-aborted transaction, but it may
                    # happen. We do not support this at the moment, and we
                    # haven't observed it in neither test nor production.
                    #
                    # This check is here for, if in in future, we reach this
                    # path, so that we can analyse the trace and adapt the
                    # code.
                    raise UnsupportedTimeoutBehaviour()

            # SESSION timeouts must be rolled back whether the code exited
            # successfully or not, because otherwise we'd leak the timeout
            # to other queries carried on within the SESSION.
            _reset_timeouts(
                using=using,
                lock_level=lock_level,
                previous_lock_timeout=previous_lock_timeout,
                previous_statement_timeout=previous_statement_timeout,
            )


def _reset_timeouts(
    using: str,
    lock_level: str,
    previous_lock_timeout: str | None,
    previous_statement_timeout: str | None,
) -> None:
    with connections[using].cursor() as cursor:
        if previous_lock_timeout is not None:
            cursor.execute(
                f"SET {lock_level} lock_timeout = %s", [previous_lock_timeout]
            )
        if previous_statement_timeout is not None:
            cursor.execute(
                f"SET {lock_level} statement_timeout = %s", [previous_statement_timeout]
            )


def _in_transaction(*, using: str) -> bool:
    """
    Return `True` if the database connection has a transaction active.
    A transaction is active if the connection is no longer in autocommit mode.
    """
    return not transaction.get_autocommit(using=using)
