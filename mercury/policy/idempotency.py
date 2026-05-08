"""Idempotency store interfaces for duplicate transaction prevention."""

from __future__ import annotations

from enum import StrEnum
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field

from mercury.models.execution import ExecutionResult


class IdempotencyStatus(StrEnum):
    """Lifecycle statuses for idempotent transaction execution."""

    IN_FLIGHT = "in_flight"
    COMPLETED = "completed"


class IdempotencyRecord(BaseModel):
    """Stored idempotency record."""

    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1)
    status: IdempotencyStatus
    result: ExecutionResult | None = None


class DuplicateTransactionError(ValueError):
    """Raised when a transaction with the same idempotency key already exists."""

    def __init__(self, record: IdempotencyRecord) -> None:
        message = f"Duplicate transaction idempotency key '{record.key}' is {record.status}."
        super().__init__(message)
        self.record = record


class InMemoryIdempotencyStore:
    """Process-local idempotency store for tests and MVP runtime.

    See deployment docs for scaling this beyond a single worker process.
    """

    def __init__(self) -> None:
        self._records: dict[str, IdempotencyRecord] = {}
        self._lock = Lock()

    def get(self, key: str) -> IdempotencyRecord | None:
        """Return a stored record, if any."""

        with self._lock:
            return self._records.get(key)

    def reserve(self, key: str) -> IdempotencyRecord:
        """Mark a key in-flight before signing and broadcasting."""

        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                raise DuplicateTransactionError(existing)
            record = IdempotencyRecord(key=key, status=IdempotencyStatus.IN_FLIGHT)
            self._records[key] = record
            return record

    def release(self, key: str) -> None:
        """Remove an ``IN_FLIGHT`` reservation so the key can be used again.

        Used when signing or broadcasting fails before a transaction is submitted.
        No-op if the key is missing or already ``COMPLETED``.
        """

        with self._lock:
            existing = self._records.get(key)
            if existing is not None and existing.status == IdempotencyStatus.IN_FLIGHT:
                del self._records[key]

    def complete(self, key: str, result: ExecutionResult) -> IdempotencyRecord:
        """Store the final execution result for a reserved key."""

        with self._lock:
            record = IdempotencyRecord(
                key=key,
                status=IdempotencyStatus.COMPLETED,
                result=result,
            )
            self._records[key] = record
            return record
