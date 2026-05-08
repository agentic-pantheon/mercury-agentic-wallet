import pytest
from mercury.models import ExecutionResult, ExecutionStatus
from mercury.policy.idempotency import (
    DuplicateTransactionError,
    IdempotencyStatus,
    InMemoryIdempotencyStore,
)


def test_first_idempotency_reservation_proceeds() -> None:
    store = InMemoryIdempotencyStore()

    record = store.reserve("send-1")

    assert record.key == "send-1"
    assert record.status == IdempotencyStatus.IN_FLIGHT


def test_duplicate_in_flight_request_is_blocked() -> None:
    store = InMemoryIdempotencyStore()
    store.reserve("send-1")

    with pytest.raises(DuplicateTransactionError) as exc_info:
        store.reserve("send-1")

    assert exc_info.value.record.status == IdempotencyStatus.IN_FLIGHT


def test_completed_request_returns_existing_record() -> None:
    store = InMemoryIdempotencyStore()
    result = ExecutionResult(
        chain="ethereum",
        chain_id=1,
        wallet_id="primary",
        status=ExecutionStatus.CONFIRMED,
        tx_hash="0x1234",
    )

    store.reserve("send-1")
    store.complete("send-1", result)

    with pytest.raises(DuplicateTransactionError) as exc_info:
        store.reserve("send-1")

    assert exc_info.value.record.status == IdempotencyStatus.COMPLETED
    assert exc_info.value.record.result == result


def test_release_removes_in_flight_so_reserve_can_repeat() -> None:
    store = InMemoryIdempotencyStore()

    store.reserve("send-1")
    store.release("send-1")

    record = store.reserve("send-1")
    assert record.status == IdempotencyStatus.IN_FLIGHT


def test_release_is_noop_for_completed_record() -> None:
    store = InMemoryIdempotencyStore()
    result = ExecutionResult(
        chain="ethereum",
        chain_id=1,
        wallet_id="primary",
        status=ExecutionStatus.CONFIRMED,
        tx_hash="0xab",
    )
    store.reserve("send-1")
    store.complete("send-1", result)

    store.release("send-1")

    with pytest.raises(DuplicateTransactionError) as exc_info:
        store.reserve("send-1")

    assert exc_info.value.record.status == IdempotencyStatus.COMPLETED
