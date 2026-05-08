from typing import Any

from mercury.graph.agent import build_transaction_graph
from mercury.graph.nodes_transaction import TransactionGraphDependencies
from mercury.models import (
    ExecutionStatus,
    GasFees,
    PreparedTransaction,
    SignedTransactionResult,
)
from mercury.models.approval import ApprovalRequest, ApprovalResult, ApprovalStatus
from mercury.models.execution import ExecutableTransaction, TransactionReceipt
from mercury.models.policy import PolicyDecision
from mercury.models.signing import SignTransactionRequest
from mercury.models.simulation import SimulationResult, SimulationStatus
from mercury.models.wallets import WalletAddressResult
from mercury.policy.idempotency import IdempotencyStatus, InMemoryIdempotencyStore
from mercury.policy.risk import TransactionPolicyEngine

PRIVATE_KEY = "0x1111111111111111111111111111111111111111111111111111111111111111"
WALLET_ADDRESS = "0x000000000000000000000000000000000000bEEF"
RECIPIENT = "0x000000000000000000000000000000000000dEaD"


def test_signing_failure_releases_idempotency_for_retry() -> None:
    events: list[str] = []
    store = InMemoryIdempotencyStore()
    signer = FakeSigner(events, sign_exception=RuntimeError("sign boom"))
    graph = build_transaction_graph(
        TransactionGraphDependencies(
            backend=FakeBackend(events),
            signer=signer,
            policy_engine=RecordingPolicyEngine(events),
            approver=FakeApprover(events, approved=True),
            idempotency_store=store,
        )
    ).compile()

    result = graph.invoke({"raw_input": _prepared_transaction()})

    assert result["execution_result"].status == ExecutionStatus.FAILED
    assert store.get("phase-6") is None

    signer.sign_exception = None
    retry = graph.invoke({"raw_input": _prepared_transaction()})
    assert retry["execution_result"].status == ExecutionStatus.CONFIRMED
    assert signer.sign_calls == 2


def test_broadcast_failure_releases_idempotency_for_retry() -> None:
    events: list[str] = []
    store = InMemoryIdempotencyStore()
    backend = FakeBackend(events, broadcast_exception=RuntimeError("broadcast boom"))
    graph = build_transaction_graph(
        TransactionGraphDependencies(
            backend=backend,
            signer=FakeSigner(events),
            policy_engine=RecordingPolicyEngine(events),
            approver=FakeApprover(events, approved=True),
            idempotency_store=store,
        )
    ).compile()

    result = graph.invoke({"raw_input": _prepared_transaction()})
    assert result["execution_result"].status == ExecutionStatus.FAILED
    assert store.get("phase-6") is None

    backend.broadcast_exception = None
    retry = graph.invoke({"raw_input": _prepared_transaction()})
    assert retry["execution_result"].status == ExecutionStatus.CONFIRMED


def test_monitor_failure_after_broadcast_records_completed_idempotency() -> None:
    events: list[str] = []
    store = InMemoryIdempotencyStore()
    backend = FakeBackend(events, monitor_exception=RuntimeError("monitor boom"))
    graph = build_transaction_graph(
        TransactionGraphDependencies(
            backend=backend,
            signer=FakeSigner(events),
            policy_engine=RecordingPolicyEngine(events),
            approver=FakeApprover(events, approved=True),
            idempotency_store=store,
        )
    ).compile()

    graph.invoke({"raw_input": _prepared_transaction()})
    record = store.get("phase-6")
    assert record is not None
    assert record.status == IdempotencyStatus.COMPLETED
    assert record.result is not None
    assert record.result.status == ExecutionStatus.FAILED

    retry = graph.invoke({"raw_input": _prepared_transaction()})
    assert retry["execution_result"] == record.result


def test_transaction_graph_orders_approval_before_sign_and_broadcast() -> None:
    events: list[str] = []
    signer = FakeSigner(events)
    backend = FakeBackend(events)
    store = InMemoryIdempotencyStore()

    graph = build_transaction_graph(
        TransactionGraphDependencies(
            backend=backend,
            signer=signer,
            policy_engine=RecordingPolicyEngine(events),
            approver=FakeApprover(events, approved=True),
            idempotency_store=store,
        )
    ).compile()

    result = graph.invoke({"raw_input": _prepared_transaction()})

    assert result["execution_result"].status == ExecutionStatus.CONFIRMED
    assert result["execution_result"].tx_hash == "0xbeef"
    assert events == [
        "address",
        "nonce",
        "gas",
        "simulate",
        "policy",
        "approval",
        "sign",
        "broadcast",
        "monitor",
    ]
    assert signer.sign_calls == 1
    assert store.get("phase-6") is not None


def test_approval_denial_prevents_signing_and_broadcast() -> None:
    events: list[str] = []
    signer = FakeSigner(events)
    backend = FakeBackend(events)
    graph = build_transaction_graph(
        TransactionGraphDependencies(
            backend=backend,
            signer=signer,
            policy_engine=RecordingPolicyEngine(events),
            approver=FakeApprover(events, approved=False),
        )
    ).compile()

    result = graph.invoke({"raw_input": _prepared_transaction()})

    assert result["execution_result"].status == ExecutionStatus.APPROVAL_DENIED
    assert signer.sign_calls == 0
    assert "sign" not in events
    assert "broadcast" not in events


def test_duplicate_in_flight_key_prevents_signing() -> None:
    events: list[str] = []
    store = InMemoryIdempotencyStore()
    store.reserve("phase-6")
    signer = FakeSigner(events)
    graph = build_transaction_graph(
        TransactionGraphDependencies(
            backend=FakeBackend(events),
            signer=signer,
            policy_engine=RecordingPolicyEngine(events),
            approver=FakeApprover(events, approved=True),
            idempotency_store=store,
        )
    ).compile()

    result = graph.invoke({"raw_input": _prepared_transaction()})

    assert result["execution_result"].status == ExecutionStatus.REJECTED
    assert "Duplicate transaction" in result["execution_result"].error.message
    assert signer.sign_calls == 0
    assert "broadcast" not in events


def test_simulation_failure_rejects_before_approval_and_signing() -> None:
    events: list[str] = []
    signer = FakeSigner(events)
    graph = build_transaction_graph(
        TransactionGraphDependencies(
            backend=FakeBackend(events, simulation_passed=False),
            signer=signer,
            policy_engine=RecordingPolicyEngine(events),
            approver=FakeApprover(events, approved=True),
        )
    ).compile()

    result = graph.invoke({"raw_input": _prepared_transaction()})

    assert result["execution_result"].status == ExecutionStatus.REJECTED
    assert "reverted" in result["execution_result"].error.message
    assert "approval" not in events
    assert signer.sign_calls == 0


def test_execution_result_does_not_include_private_key_material() -> None:
    events: list[str] = []
    graph = build_transaction_graph(
        TransactionGraphDependencies(
            backend=FakeBackend(events),
            signer=FakeSigner(events),
            policy_engine=RecordingPolicyEngine(events),
            approver=FakeApprover(events, approved=True),
        )
    ).compile()

    result = graph.invoke({"raw_input": _prepared_transaction()})
    serialized = result["execution_result"].model_dump_json()

    assert PRIVATE_KEY not in serialized
    assert "private_key" not in serialized


class RecordingPolicyEngine(TransactionPolicyEngine):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events

    def evaluate(
        self,
        transaction: ExecutableTransaction,
        simulation: SimulationResult | None,
    ) -> PolicyDecision:
        self._events.append("policy")
        return super().evaluate(transaction, simulation)


class FakeSigner:
    def __init__(
        self,
        events: list[str],
        *,
        sign_exception: BaseException | None = None,
    ) -> None:
        self._events = events
        self.sign_calls = 0
        self.sign_exception = sign_exception
        self._private_key = PRIVATE_KEY

    def get_wallet_address(self, wallet_id: str) -> WalletAddressResult:
        self._events.append("address")
        return WalletAddressResult(wallet_id=wallet_id, address=WALLET_ADDRESS)

    def sign_transaction(self, request: SignTransactionRequest) -> SignedTransactionResult:
        self._events.append("sign")
        self.sign_calls += 1
        if self.sign_exception is not None:
            raise self.sign_exception
        assert request.prepared_transaction.transaction["to"] == RECIPIENT
        return SignedTransactionResult(
            wallet_id=request.wallet.wallet_id,
            chain_id=request.chain_id,
            signer_address=WALLET_ADDRESS,
            raw_transaction_hex="0x02",
            tx_hash="0xabcd",
        )


class FakeApprover:
    def __init__(self, events: list[str], *, approved: bool) -> None:
        self._events = events
        self._approved = approved

    def request_approval(self, request: ApprovalRequest) -> ApprovalResult:
        self._events.append("approval")
        assert request.to == RECIPIENT
        if self._approved:
            return ApprovalResult(
                status=ApprovalStatus.APPROVED,
                reason="approved",
                approved_by="test",
            )
        return ApprovalResult(status=ApprovalStatus.DENIED, reason="denied")


class FakeBackend:
    def __init__(
        self,
        events: list[str],
        *,
        simulation_passed: bool = True,
        broadcast_exception: BaseException | None = None,
        monitor_exception: BaseException | None = None,
    ) -> None:
        self._events = events
        self._simulation_passed = simulation_passed
        self.broadcast_exception = broadcast_exception
        self.monitor_exception = monitor_exception

    def resolve_chain_id(self, transaction: PreparedTransaction) -> int:
        return 1

    def lookup_nonce(self, transaction: PreparedTransaction, wallet_address: str) -> int:
        self._events.append("nonce")
        assert wallet_address == WALLET_ADDRESS
        return 7

    def populate_gas(self, transaction: PreparedTransaction | ExecutableTransaction) -> GasFees:
        self._events.append("gas")
        return GasFees(gas_limit=21_000, gas_price=1_000_000_000)

    def simulate(self, transaction: ExecutableTransaction) -> SimulationResult:
        self._events.append("simulate")
        if not self._simulation_passed:
            return SimulationResult(status=SimulationStatus.FAILED, reason="execution reverted")
        return SimulationResult(status=SimulationStatus.PASSED, gas_estimate=21_000)

    def broadcast(self, signed_transaction: SignedTransactionResult) -> str:
        self._events.append("broadcast")
        assert signed_transaction.raw_transaction_hex == "0x02"
        if self.broadcast_exception is not None:
            raise self.broadcast_exception
        return "0xbeef"

    def wait_for_receipt(
        self,
        *,
        chain: str,
        tx_hash: str,
        timeout_seconds: float,
        confirmations: int,
    ) -> TransactionReceipt:
        self._events.append("monitor")
        assert tx_hash == "0xbeef"
        if self.monitor_exception is not None:
            raise self.monitor_exception
        return TransactionReceipt(
            tx_hash=tx_hash,
            status=ExecutionStatus.CONFIRMED,
            block_number=123,
            gas_used=21_000,
        )


def _prepared_transaction(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "wallet_id": "primary",
        "chain": "ethereum",
        "chain_id": 1,
        "to": RECIPIENT,
        "value_wei": 1,
        "data": "0x",
        "idempotency_key": "phase-6",
        "metadata": {"action": "native_transfer", "recipient_address": RECIPIENT},
    }
    data.update(overrides)
    return data
