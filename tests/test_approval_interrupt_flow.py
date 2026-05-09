"""LangGraph interrupt/resume approval flow (Phase 2)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, Interrupt
from mercury.config import MercurySettings
from mercury.graph.agent import build_native_transaction_graph, build_transaction_graph
from mercury.graph.nodes_native import NativeGraphDependencies
from mercury.graph.nodes_transaction import (
    APPROVAL_INTERRUPT_KIND,
    TransactionGraphDependencies,
)
from mercury.graph.runtime import MercuryGraphRuntime
from mercury.invoke import invoke_mercury, invoke_response_from_state
from mercury.models import ExecutionStatus, GasFees, SignedTransactionResult
from mercury.models.approval import ApprovalStatus
from mercury.models.execution import ExecutableTransaction, PreparedTransaction, TransactionReceipt
from mercury.models.simulation import SimulationResult, SimulationStatus
from mercury.policy.idempotency import InMemoryIdempotencyStore
from mercury.service.models import MercuryInvokeRequest
from mercury.tools.transactions import PlaceholderTransactionApprover

from tests.test_transaction_pipeline import FakeSigner, RecordingPolicyEngine

WALLET_ADDRESS = "0x000000000000000000000000000000000000bEEF"
RECIPIENT = "0x000000000000000000000000000000000000dEaD"


class CountingBackend:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def resolve_chain_id(self, transaction: PreparedTransaction) -> int:
        return transaction.chain_id or 1

    def lookup_nonce(self, transaction: PreparedTransaction, wallet_address: str) -> int:
        self._events.append("nonce")
        assert wallet_address == WALLET_ADDRESS
        return 7

    def populate_gas(self, transaction: PreparedTransaction | ExecutableTransaction) -> GasFees:
        self._events.append("gas")
        return GasFees(gas_limit=21_000, gas_price=1_000_000_000)

    def simulate(self, transaction: ExecutableTransaction) -> SimulationResult:
        self._events.append("simulate")
        return SimulationResult(status=SimulationStatus.PASSED, gas_estimate=21_000)

    def broadcast(self, signed_transaction: SignedTransactionResult) -> str:
        self._events.append("broadcast")
        assert signed_transaction.raw_transaction_hex == "0x02"
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
        return TransactionReceipt(
            tx_hash=tx_hash,
            status=ExecutionStatus.CONFIRMED,
            block_number=123,
            gas_used=21_000,
        )


def _transaction_deps(
    events: list[str],
    signer: FakeSigner,
    *,
    interrupt_approval: bool,
) -> TransactionGraphDependencies:
    return TransactionGraphDependencies(
        backend=CountingBackend(events),
        signer=signer,
        policy_engine=RecordingPolicyEngine(events),
        approver=PlaceholderTransactionApprover(),
        idempotency_store=InMemoryIdempotencyStore(),
        interrupt_approval=interrupt_approval,
    )


def _prepared_transaction(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "wallet_id": "primary",
        "chain": "ethereum",
        "chain_id": 1,
        "to": RECIPIENT,
        "value_wei": 1,
        "data": "0x",
        "idempotency_key": "phase-interrupt",
        "metadata": {"action": "native_transfer", "recipient_address": RECIPIENT},
    }
    data.update(overrides)
    return data


def test_legacy_interrupt_disabled_returns_denied_without_interrupt() -> None:
    events: list[str] = []
    signer = FakeSigner(events)
    graph = build_transaction_graph(
        _transaction_deps(events, signer, interrupt_approval=False),
    ).compile(checkpointer=InMemorySaver())

    result = graph.invoke({"raw_input": _prepared_transaction()}, config=_cfg("legacy-1"))

    assert "__interrupt__" not in result
    assert result["execution_result"].status == ExecutionStatus.APPROVAL_DENIED
    assert signer.sign_calls == 0
    assert "simulate" in events


def test_interrupt_first_invoke_exposes_interrupt_without_signing() -> None:
    events: list[str] = []
    signer = FakeSigner(events)
    graph = build_transaction_graph(
        _transaction_deps(events, signer, interrupt_approval=True),
    ).compile(checkpointer=InMemorySaver())

    result = graph.invoke({"raw_input": _prepared_transaction()}, config=_cfg("intr-1"))

    assert "__interrupt__" in result
    assert signer.sign_calls == 0
    assert events.count("simulate") == 1


def test_resume_after_interrupt_completes_without_second_simulation() -> None:
    events: list[str] = []
    signer = FakeSigner(events)
    cfg = _cfg("resume-1")
    graph = build_transaction_graph(
        _transaction_deps(events, signer, interrupt_approval=True),
    ).compile(checkpointer=InMemorySaver())

    graph.invoke({"raw_input": _prepared_transaction()}, config=cfg)

    assert events.count("simulate") == 1

    final = graph.invoke(
        Command(
            resume={
                "status": ApprovalStatus.APPROVED.value,
                "idempotency_key": "phase-interrupt",
                "approved_by": "human",
            }
        ),
        config=cfg,
    )

    assert final["execution_result"].status == ExecutionStatus.CONFIRMED
    assert events.count("simulate") == 1
    assert signer.sign_calls == 1


def test_resume_denied_does_not_sign() -> None:
    events: list[str] = []
    signer = FakeSigner(events)
    cfg = _cfg("deny-1")
    graph = build_transaction_graph(
        _transaction_deps(events, signer, interrupt_approval=True),
    ).compile(checkpointer=InMemorySaver())

    graph.invoke({"raw_input": _prepared_transaction()}, config=cfg)
    final = graph.invoke(
        Command(resume={"status": ApprovalStatus.DENIED.value, "reason": "no"}),
        config=cfg,
    )

    assert final["execution_result"].status == ExecutionStatus.APPROVAL_DENIED
    assert signer.sign_calls == 0


def test_resume_idempotency_key_mismatch_denies_without_signing() -> None:
    events: list[str] = []
    signer = FakeSigner(events)
    cfg = _cfg("bad-key")
    graph = build_transaction_graph(
        _transaction_deps(events, signer, interrupt_approval=True),
    ).compile(checkpointer=InMemorySaver())

    graph.invoke({"raw_input": _prepared_transaction()}, config=cfg)
    final = graph.invoke(
        Command(
            resume={
                "status": ApprovalStatus.APPROVED.value,
                "idempotency_key": "wrong-key",
            }
        ),
        config=cfg,
    )

    assert final["execution_result"].status == ExecutionStatus.APPROVAL_DENIED
    assert "idempotency" in final["execution_result"].error.message.lower()
    assert signer.sign_calls == 0


def test_resume_missing_idempotency_key_denies_without_signing() -> None:
    events: list[str] = []
    signer = FakeSigner(events)
    cfg = _cfg("missing-key")
    graph = build_transaction_graph(
        _transaction_deps(events, signer, interrupt_approval=True),
    ).compile(checkpointer=InMemorySaver())

    graph.invoke({"raw_input": _prepared_transaction()}, config=cfg)
    final = graph.invoke(
        Command(resume={"status": ApprovalStatus.APPROVED.value}),
        config=cfg,
    )

    assert final["execution_result"].status == ExecutionStatus.APPROVAL_DENIED
    assert "idempotency key" in final["execution_result"].error.message.lower()
    assert signer.sign_calls == 0


def test_resume_transaction_fingerprint_mismatch_denies_without_signing() -> None:
    events: list[str] = []
    signer = FakeSigner(events)
    cfg = _cfg("bad-fingerprint")
    graph = build_transaction_graph(
        _transaction_deps(events, signer, interrupt_approval=True),
    ).compile(checkpointer=InMemorySaver())

    graph.invoke({"raw_input": _prepared_transaction()}, config=cfg)
    final = graph.invoke(
        Command(
            resume={
                "status": ApprovalStatus.APPROVED.value,
                "idempotency_key": "phase-interrupt",
                "transaction_fingerprint": "not-the-pending-tx",
            }
        ),
        config=cfg,
    )

    assert final["execution_result"].status == ExecutionStatus.APPROVAL_DENIED
    assert "fingerprint" in final["execution_result"].error.message.lower()
    assert signer.sign_calls == 0


def test_invoke_response_maps_transaction_interrupt() -> None:
    intr_val = {
        "kind": APPROVAL_INTERRUPT_KIND,
        "chain": "ethereum",
        "idempotency_key": "phase-interrupt",
        "from": WALLET_ADDRESS,
        "to": RECIPIENT,
        "value_wei": "1",
        "data_preview": "0x",
        "chain_id": 1,
        "nonce": 7,
    }
    state = {
        "__interrupt__": [Interrupt(value=intr_val, id="deadbeef")],
        "chain_name": "ethereum",
    }
    resp = invoke_response_from_state(state, request_id="rid-x", fallback_chain="ethereum")
    assert resp.status == "approval_required"
    assert resp.approval_required is True
    assert resp.request_id == "rid-x"
    assert resp.approval_payload is not None
    assert resp.approval_payload.get("kind") == APPROVAL_INTERRUPT_KIND


def test_invoke_mercury_resume_native_transfer_roundtrip() -> None:
    events: list[str] = []
    signer = FakeSigner(events)
    tx_deps = _transaction_deps(events, signer, interrupt_approval=True)
    native_graph = build_native_transaction_graph(
        NativeGraphDependencies(address_resolver=signer),
        tx_deps,
    ).compile(checkpointer=InMemorySaver())

    trap = MagicMock()
    trap.invoke.side_effect = AssertionError("unexpected graph selection")
    trap.stream = None

    runtime = MercuryGraphRuntime(
        read_graph=trap,
        erc20_graph=trap,
        native_graph=native_graph,
        swap_graph=trap,
        runtime_settings=MercurySettings(interrupt_approval=True),
    )

    req_id = "req-native-interrupt"
    first = MercuryInvokeRequest(
        user_id="u1",
        wallet_id="primary",
        intent={
            "kind": "native_transfer",
            "recipient_address": RECIPIENT,
            "amount": "0.000000001",
        },
        chain="base",
        request_id=req_id,
        idempotency_key="native-interrupt-1",
    )
    r1 = invoke_mercury(runtime, first)
    assert r1.request_id == req_id
    assert r1.status == "approval_required"
    assert r1.approval_required is True
    assert r1.tx_hash is None

    second = MercuryInvokeRequest(
        user_id="u1",
        wallet_id="primary",
        intent={
            "kind": "native_transfer",
            "recipient_address": RECIPIENT,
            "amount": "0.000000001",
        },
        chain="base",
        request_id=req_id,
        idempotency_key="native-interrupt-1",
        approval_response={
            "status": ApprovalStatus.APPROVED.value,
            "idempotency_key": "native-interrupt-1",
            "approved_by": "tester",
        },
    )
    r2 = invoke_mercury(runtime, second)
    assert r2.status == "confirmed"
    assert r2.tx_hash == "0xbeef"
    assert events.count("simulate") == 1


def test_runtime_errors_when_pending_interrupt_but_no_resume_payload() -> None:
    events: list[str] = []
    signer = FakeSigner(events)
    tx_deps = _transaction_deps(events, signer, interrupt_approval=True)
    native_graph = build_native_transaction_graph(
        NativeGraphDependencies(address_resolver=signer),
        tx_deps,
    ).compile(checkpointer=InMemorySaver())

    trap = MagicMock()
    trap.invoke.side_effect = AssertionError("unexpected graph selection")
    trap.stream = None

    runtime = MercuryGraphRuntime(
        read_graph=trap,
        erc20_graph=trap,
        native_graph=native_graph,
        swap_graph=trap,
        runtime_settings=MercurySettings(interrupt_approval=True),
    )

    rid = "pending-thread"
    payload = {
        "kind": "native_transfer",
        "recipient_address": RECIPIENT,
        "amount": "0.000000001",
        "chain": "base",
        "wallet_id": "primary",
        "idempotency_key": "pending-1",
        "metadata": {"user_id": "u1"},
    }

    runtime.invoke({"request_id": rid, "raw_input": payload})

    duplicate = runtime.invoke({"request_id": rid, "raw_input": payload})
    assert duplicate.get("error") is not None
    assert duplicate["error"].code == "interrupt_resume_required"


def _cfg(thread: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread}}
