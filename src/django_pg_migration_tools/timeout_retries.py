import dataclasses
import datetime
import importlib
import io
import time
from typing import Any, Protocol, cast

from typing_extensions import Self

from django_pg_migration_tools import timeouts


@dataclasses.dataclass
class RetryState:
    current_exception: timeouts.DBTimeoutError
    lock_timeouts_count: int
    stdout: io.StringIO
    time_since_start: datetime.timedelta
    database: str


class RetryCallback(Protocol):
    def __call__(self, retry_state: RetryState, /) -> None: ...  # pragma: no cover


@dataclasses.dataclass(kw_only=True)
class TimeoutRetryOptions:
    max_retries: int
    exp: int
    max_wait: datetime.timedelta
    min_wait: datetime.timedelta

    def validate(self) -> None:
        if (self.min_wait is not None and self.max_wait is not None) and (
            self.min_wait > self.max_wait
        ):
            raise ValueError(
                "The minimum wait cannot be greater than the maximum wait for retries."
            )


@dataclasses.dataclass(frozen=True, kw_only=True)
class MigrationTimeoutOptions:
    lock_timeout: datetime.timedelta | None
    statement_timeout: datetime.timedelta | None
    lock_retry_options: TimeoutRetryOptions
    retry_callback: RetryCallback | None

    @classmethod
    def from_dictionary(cls, options: dict[str, Any]) -> Self:
        return cls(
            lock_timeout=_Parser.optional_positive_ms_to_timedelta(
                options.pop("lock_timeout_in_ms", None)
            ),
            statement_timeout=_Parser.optional_positive_ms_to_timedelta(
                options.pop("statement_timeout_in_ms", None),
            ),
            lock_retry_options=TimeoutRetryOptions(
                max_retries=_Parser.required_positive_int(
                    options.pop("lock_timeout_max_retries")
                ),
                exp=_Parser.required_positive_int(
                    options.pop("lock_timeout_retry_exp")
                ),
                max_wait=_Parser.required_positive_ms_to_timedelta(
                    options.pop("lock_timeout_retry_max_wait_in_ms")
                ),
                min_wait=_Parser.required_positive_ms_to_timedelta(
                    options.pop("lock_timeout_retry_min_wait_in_ms")
                ),
            ),
            retry_callback=_Parser.optional_retry_callback(
                options.pop("retry_callback_path", None)
            ),
        )

    def validate(self) -> None:
        if self.statement_timeout is None and self.lock_timeout is None:
            raise ValueError(
                "At least one of --lock-timeout-in-ms or --statement-timeout-in-ms "
                "must be specified."
            )
        self.lock_retry_options.validate()


class MigrateRetryStrategy:
    timeout_options: MigrationTimeoutOptions
    retries: int

    def __init__(self, timeout_options: MigrationTimeoutOptions):
        self.timeout_options = timeout_options
        self.retries = 0

    def wait(self) -> None:
        exp = self.timeout_options.lock_retry_options.exp
        min_wait = self.timeout_options.lock_retry_options.min_wait
        max_wait = self.timeout_options.lock_retry_options.max_wait

        if not self.can_migrate():
            # No point waiting if we can't migrate.
            return
        try:
            # self.retries is an integer, but it is turned into a float here
            # because a huge exponentiation in Python between integers
            # **never** overflows. Instead, the CPU is left trying to calculate
            # the result forever and it will eventually return a memory error
            # instead. Which we absolutely do not want. Please see:
            # https://docs.python.org/3.12/library/exceptions.html#OverflowError
            result = exp ** (float(self.retries))
        except OverflowError:
            result = max_wait.total_seconds()
        wait = max(min_wait.total_seconds(), min(result, max_wait.total_seconds()))
        time.sleep(wait)

    def attempt_callback(
        self,
        current_exception: timeouts.DBTimeoutError,
        stdout: io.StringIO,
        start_time: float,
        database: str,
    ) -> None:
        if self.timeout_options.retry_callback:
            self.timeout_options.retry_callback(
                RetryState(
                    current_exception=current_exception,
                    lock_timeouts_count=self.retries,
                    stdout=stdout,
                    time_since_start=datetime.timedelta(
                        seconds=time.time() - start_time
                    ),
                    database=database,
                )
            )

    def can_migrate(self) -> bool:
        if self.retries == 0:
            # This is the first time migration will run.
            return True
        return bool(self.retries <= self.timeout_options.lock_retry_options.max_retries)

    def increment_retry_count(self) -> None:
        self.retries += 1


class _Parser:
    @classmethod
    def optional_positive_ms_to_timedelta(
        cls, value: int | None
    ) -> datetime.timedelta | None:
        if value is None:
            return None
        return cls.required_positive_ms_to_timedelta(value)

    @classmethod
    def required_positive_ms_to_timedelta(cls, value: int) -> datetime.timedelta:
        value = cls.required_positive_int(value)
        return datetime.timedelta(milliseconds=value)

    @classmethod
    def required_positive_int(cls, value: Any) -> int:
        if (not isinstance(value, int)) or (value < 0):
            raise ValueError(f"{value} is not a positive integer.")
        return value

    @classmethod
    def optional_retry_callback(cls, value: str | None) -> RetryCallback | None:
        if not value:
            return None

        assert "." in value
        module, attr_name = value.rsplit(".", 1)

        # This raises ModuleNotFoundError, which gives a good explanation
        # of the error already (see tests). We don't have to wrap this into
        # our own exception.
        callback_module = importlib.import_module(module)
        callback = getattr(callback_module, attr_name)
        assert callable(callback)
        return cast(RetryCallback, callback)
