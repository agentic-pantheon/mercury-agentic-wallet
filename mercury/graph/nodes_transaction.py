"""LangGraph nodes for the generic transaction execution pipeline."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from langgraph.types import interrupt

from mercury.graph.state import MercuryState
from mercury.models.approval import ApprovalResult, ApprovalStatus
from mercury.models.errors import (
    MercuryErrorInfo,
    approval_denied,
    idempotency_conflict,
    internal_error,
    normalize_exception,
    policy_rejected,
)
from mercury.models.execution import (
    ExecutableTransaction,
    ExecutionResult,
    ExecutionStatus,
    PreparedTransaction,
    TransactionReceipt,
)
from mercury.models.policy import PolicyDecision, PolicyDecisionStatus
from mercury.models.simulation import SimulationResult, SimulationStatus
from mercury.policy.idempotency import (
    DuplicateTransactionError,
    InMemoryIdempotencyStore,
)
from mercury.policy.risk import TransactionPolicyEngine
from mercury.tools.transactions import (
    PlaceholderTransactionApprover,
    TransactionApprover,
    TransactionBackend,
    TransactionSigner,
    build_approval_request,
    sign_executable_transaction,
)


@dataclass
class TransactionGraphDependencies:
    """Injectable transaction graph dependencies."""

    backend: TransactionBackend
    signer: TransactionSigner
    policy_engine: TransactionPolicyEngine = field(default_factory=TransactionPolicyEngine)
    approver: TransactionApprover = field(default_factory=PlaceholderTransactionApprover)
    idempotency_store: InMemoryIdempotencyStore = field(default_factory=InMemoryIdempotencyStore)
    receipt_timeout_seconds: float = 120
    receipt_confirmations: int = 0
    interrupt_approval: bool = False


APPROVAL_INTERRUPT_KIND = "mercury_transaction_approval"


def build_transaction_approval_interrupt_payload(tx: ExecutableTransaction) -> dict[str, Any]:
    """Serializable, secret-safe payload surfaced to clients when approval interrupts."""

    raw_data = tx.data if isinstance(tx.data, str) else ""
    preview_len = 66
    data_preview = raw_data[:preview_len]
    if len(raw_data) > preview_len:
        data_preview = f"{data_preview}..."
    idem = tx.idempotency_key
    idempotency_key = idem if isinstance(idem, str) else ""
    return {
        "kind": APPROVAL_INTERRUPT_KIND,
        "idempotency_key": idempotency_key,
        "chain": tx.chain,
        "chain_id": tx.chain_id,
        "from": tx.from_address,
        "to": tx.to,
        "value_wei": str(tx.value_wei) if tx.value_wei is not None else "0",
        "data_preview": data_preview,
        "nonce": tx.nonce,
        "transaction_fingerprint": _transaction_fingerprint(tx),
    }


def approval_result_from_interrupt_resume(
    raw: Any,
    *,
    expected_idempotency_key: str | None,
    expected_transaction_fingerprint: str | None = None,
) -> ApprovalResult:
    """Map a client resume payload (same shape as ``approval_response`` metadata) to a result."""

    if not isinstance(raw, Mapping):
        return ApprovalResult(
            status=ApprovalStatus.DENIED,
            reason="Approval resume payload must be a JSON object.",
        )
    mapping = dict(raw)
    status_raw = str(mapping.get("status", "")).strip().lower()
    if status_raw == ApprovalStatus.DENIED.value:
        return ApprovalResult(
            status=ApprovalStatus.DENIED,
            reason=str(mapping.get("reason") or "Approval denied."),
        )
    if status_raw != ApprovalStatus.APPROVED.value:
        return ApprovalResult(
            status=ApprovalStatus.DENIED,
            reason="Unsupported approval status in resume payload.",
        )
    if expected_idempotency_key is None or not str(expected_idempotency_key).strip():
        return ApprovalResult(
            status=ApprovalStatus.DENIED,
            reason="Cannot approve without a transaction idempotency key.",
        )
    raw_key = mapping.get("idempotency_key")
    if raw_key is None or str(raw_key) != str(expected_idempotency_key):
        return ApprovalResult(
            status=ApprovalStatus.DENIED,
            reason="Approval idempotency key is required and must match the transaction.",
        )
    raw_fingerprint = mapping.get("transaction_fingerprint")
    if (
        expected_transaction_fingerprint is not None
        and raw_fingerprint is not None
        and str(raw_fingerprint) != expected_transaction_fingerprint
    ):
        return ApprovalResult(
            status=ApprovalStatus.DENIED,
            reason="Approval transaction fingerprint does not match the pending transaction.",
        )
    approved_by = mapping.get("approved_by")
    reason = str(mapping.get("reason") or "Approved via interrupt resume.")
    return ApprovalResult(
        status=ApprovalStatus.APPROVED,
        reason=reason,
        approved_by=str(approved_by) if approved_by is not None else None,
    )


def make_resolve_nonce_node(
    deps: TransactionGraphDependencies,
) -> Callable[[MercuryState], MercuryState]:
    """Create a node that resolves wallet address, chain ID, and nonce."""

    def resolve_nonce(state: MercuryState) -> MercuryState:
        try:
            transaction = _prepared_from_state(state)
            wallet = deps.signer.get_wallet_address(transaction.wallet_id)
            chain_id = transaction.chain_id or deps.backend.resolve_chain_id(transaction)
            prepared = transaction.model_copy(
                update={
                    "chain_id": chain_id,
                    "from_address": transaction.from_address or wallet.address,
                    "nonce": transaction.nonce
                    if transaction.nonce is not None
                    else deps.backend.lookup_nonce(transaction, wallet.address),
                }
            )
            return {
                "prepared_transaction": prepared,
                "wallet_address": wallet.address,
                "chain_name": prepared.chain,
            }
        except Exception as exc:
            return {"error": normalize_exception(exc, stage="resolve_nonce")}

    return resolve_nonce


def make_populate_gas_node(
    deps: TransactionGraphDependencies,
) -> Callable[[MercuryState], MercuryState]:
    """Create a node that estimates gas and builds an executable transaction."""

    def populate_gas(state: MercuryState) -> MercuryState:
        if state.get("error"):
            return {}
        try:
            prepared = _prepared_from_state(state)
            if prepared.chain_id is None or prepared.nonce is None:
                raise ValueError("Transaction nonce and chain_id must be resolved before gas.")
            gas = deps.backend.populate_gas(prepared)
            executable = ExecutableTransaction(
                wallet_id=prepared.wallet_id,
                chain=prepared.chain,
                chain_id=prepared.chain_id,
                from_address=prepared.from_address,
                to=prepared.to,
                value_wei=prepared.value_wei,
                data=prepared.data,
                nonce=prepared.nonce,
                gas=gas,
                idempotency_key=prepared.idempotency_key,
                metadata=prepared.metadata,
            )
            return {"executable_transaction": executable}
        except Exception as exc:
            return {"error": normalize_exception(exc, stage="populate_gas")}

    return populate_gas


def make_simulate_transaction_node(
    deps: TransactionGraphDependencies,
) -> Callable[[MercuryState], MercuryState]:
    """Create a node that runs transaction preflight simulation."""

    def simulate_transaction(state: MercuryState) -> MercuryState:
        if state.get("error"):
            return {}
        try:
            executable = _executable_from_state(state)
            simulation = deps.backend.simulate(executable)
            return {"simulation_result": simulation}
        except Exception as exc:
            err = normalize_exception(exc, code="simulation_failed", stage="simulate_transaction")
            return {
                "simulation_result": SimulationResult(
                    status=SimulationStatus.FAILED,
                    reason=err.message,
                )
            }

    return simulate_transaction


def make_policy_node(deps: TransactionGraphDependencies) -> Callable[[MercuryState], MercuryState]:
    """Create a node that evaluates transaction policy."""

    def evaluate_policy(state: MercuryState) -> MercuryState:
        if state.get("error"):
            return {"policy_decision": _reject_decision(state["error"])}
        try:
            decision = deps.policy_engine.evaluate(
                _executable_from_state(state),
                state.get("simulation_result"),
            )
            return {"policy_decision": decision}
        except Exception as exc:
            return {
                "policy_decision": _reject_decision(
                    normalize_exception(exc, stage="evaluate_policy")
                )
            }

    return evaluate_policy


def make_approval_node(
    deps: TransactionGraphDependencies,
) -> Callable[[MercuryState], MercuryState]:
    """Create a node that requests human approval before signing."""

    def request_approval(state: MercuryState) -> MercuryState:
        try:
            executable = _executable_from_state(state)
            approval = deps.approver.request_approval(build_approval_request(executable))
        except Exception as exc:
            return {
                "execution_result": _result_from_state(
                    state,
                    status=ExecutionStatus.APPROVAL_DENIED,
                    error=normalize_exception(exc, stage="request_approval"),
                )
            }

        if deps.interrupt_approval and approval.status == ApprovalStatus.REQUIRED:
            payload = build_transaction_approval_interrupt_payload(executable)
            resume_raw = interrupt(payload)
            approval = approval_result_from_interrupt_resume(
                resume_raw,
                expected_idempotency_key=executable.idempotency_key,
                expected_transaction_fingerprint=payload.get("transaction_fingerprint"),
            )

        if approval.status != ApprovalStatus.APPROVED:
            return {
                "approval_result": approval,
                "execution_result": _result_from_state(
                    state,
                    status=ExecutionStatus.APPROVAL_DENIED,
                    error=approval_denied(message=approval.reason, stage="request_approval"),
                ),
            }
        return {"approval_result": approval}

    return request_approval


def _transaction_fingerprint(tx: ExecutableTransaction) -> str:
    """Stable hash for binding an approval resume to the checkpointed transaction."""

    payload = {
        "chain": tx.chain,
        "chain_id": tx.chain_id,
        "from_address": tx.from_address,
        "to": tx.to,
        "value_wei": str(tx.value_wei) if tx.value_wei is not None else "0",
        "data": tx.data,
        "nonce": tx.nonce,
        "idempotency_key": tx.idempotency_key,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_idempotency_node(
    deps: TransactionGraphDependencies,
) -> Callable[[MercuryState], MercuryState]:
    """Create a node that reserves an idempotency key before signing."""

    def check_idempotency(state: MercuryState) -> MercuryState:
        try:
            transaction = _executable_from_state(state)
            if transaction.idempotency_key is None:
                raise ValueError("Idempotency key is required before signing.")
            deps.idempotency_store.reserve(transaction.idempotency_key)
            return {}
        except DuplicateTransactionError as exc:
            if exc.record.result is not None:
                return {"execution_result": exc.record.result}
            return {
                "execution_result": _result_from_state(
                    state,
                    status=ExecutionStatus.REJECTED,
                    error=idempotency_conflict(stage="check_idempotency"),
                )
            }
        except Exception as exc:
            return {
                "execution_result": _result_from_state(
                    state,
                    status=ExecutionStatus.REJECTED,
                    error=normalize_exception(exc, stage="check_idempotency"),
                )
            }

    return check_idempotency


def make_sign_transaction_node(
    deps: TransactionGraphDependencies,
) -> Callable[[MercuryState], MercuryState]:
    """Create a node that signs only after policy, approval, and idempotency."""

    def sign_transaction(state: MercuryState) -> MercuryState:
        try:
            signed = sign_executable_transaction(
                signer=deps.signer,
                transaction=_executable_from_state(state),
            )
            return {"signed_transaction": signed}
        except Exception as exc:
            _release_idempotency_after_pre_broadcast_failure(deps, state)
            return {
                "execution_result": _result_from_state(
                    state,
                    status=ExecutionStatus.FAILED,
                    error=normalize_exception(exc, code="signing_failed", stage="sign_transaction"),
                )
            }

    return sign_transaction


def make_broadcast_transaction_node(
    deps: TransactionGraphDependencies,
) -> Callable[[MercuryState], MercuryState]:
    """Create a node that broadcasts a signed transaction."""

    def broadcast_transaction(state: MercuryState) -> MercuryState:
        if state.get("execution_result") is not None:
            return {}
        try:
            tx_hash = deps.backend.broadcast(state["signed_transaction"])
            return {"tx_hash": tx_hash}
        except Exception as exc:
            _release_idempotency_after_pre_broadcast_failure(deps, state)
            return {
                "execution_result": _result_from_state(
                    state,
                    status=ExecutionStatus.FAILED,
                    error=normalize_exception(
                        exc, code="broadcast_failed", stage="broadcast_transaction"
                    ),
                )
            }

    return broadcast_transaction


def make_monitor_receipt_node(
    deps: TransactionGraphDependencies,
) -> Callable[[MercuryState], MercuryState]:
    """Create a node that monitors a broadcast transaction receipt."""

    def monitor_receipt(state: MercuryState) -> MercuryState:
        if state.get("execution_result") is not None:
            return {}
        try:
            transaction = _executable_from_state(state)
            tx_hash = state["tx_hash"]
            receipt = deps.backend.wait_for_receipt(
                chain=transaction.chain,
                tx_hash=tx_hash,
                timeout_seconds=deps.receipt_timeout_seconds,
                confirmations=deps.receipt_confirmations,
            )
            result = _result_from_receipt(state, receipt)
            if transaction.idempotency_key is not None:
                deps.idempotency_store.complete(transaction.idempotency_key, result)
            return {"execution_result": result}
        except Exception as exc:
            result = _result_from_state(
                state,
                status=ExecutionStatus.FAILED,
                tx_hash=state.get("tx_hash"),
                error=normalize_exception(exc, stage="monitor_receipt"),
            )
            _finalize_idempotency_after_monitor_failure(deps, state, result)
            return {"execution_result": result}

    return monitor_receipt


def reject_transaction(state: MercuryState) -> MercuryState:
    """Create a normalized rejected execution result."""

    if state.get("execution_result") is not None:
        return {}
    decision = state.get("policy_decision")
    if decision is not None:
        err: MercuryErrorInfo = policy_rejected(message=decision.reason, stage="reject_transaction")
    else:
        raw = state.get("error")
        if isinstance(raw, MercuryErrorInfo):
            err = raw
        elif raw is not None:
            err = policy_rejected(message=str(raw), stage="reject_transaction")
        else:
            err = policy_rejected(message="Transaction rejected.", stage="reject_transaction")
    return {
        "execution_result": _result_from_state(
            state,
            status=ExecutionStatus.REJECTED,
            error=err,
        )
    }


def _idempotency_key_from_state(state: MercuryState) -> str | None:
    transaction = state.get("executable_transaction")
    if isinstance(transaction, ExecutableTransaction):
        key = transaction.idempotency_key
        return key if isinstance(key, str) and key else None
    return None


def _release_idempotency_after_pre_broadcast_failure(
    deps: TransactionGraphDependencies,
    state: MercuryState,
) -> None:
    """Clear ``IN_FLIGHT`` when no transaction was submitted (sign or broadcast failed)."""

    key = _idempotency_key_from_state(state)
    if key is not None:
        deps.idempotency_store.release(key)


def _finalize_idempotency_after_monitor_failure(
    deps: TransactionGraphDependencies,
    state: MercuryState,
    result: ExecutionResult,
) -> None:
    """After broadcast, never release the key: replay must not resubmit.

    If we have a tx hash, record the terminal result. Otherwise clear ``IN_FLIGHT``
    (unexpected graph state).
    """

    key = _idempotency_key_from_state(state)
    if key is None:
        return
    tx_hash = state.get("tx_hash")
    if isinstance(tx_hash, str) and tx_hash.strip():
        deps.idempotency_store.complete(key, result)
    else:
        deps.idempotency_store.release(key)


def _prepared_from_state(state: MercuryState) -> PreparedTransaction:
    raw = state.get("prepared_transaction", state.get("raw_input"))
    if isinstance(raw, PreparedTransaction):
        return raw
    if isinstance(raw, dict):
        return PreparedTransaction.model_validate(raw)
    raise ValueError("Transaction graph requires a prepared transaction.")


def _executable_from_state(state: MercuryState) -> ExecutableTransaction:
    executable = state.get("executable_transaction")
    if not isinstance(executable, ExecutableTransaction):
        raise ValueError("Transaction graph requires an executable transaction.")
    return executable


def _reject_decision(reason: str | MercuryErrorInfo) -> PolicyDecision:
    text = reason.message if isinstance(reason, MercuryErrorInfo) else reason
    return PolicyDecision(status=PolicyDecisionStatus.REJECTED, reason=text)


def _result_from_receipt(state: MercuryState, receipt: TransactionReceipt) -> ExecutionResult:
    return _result_from_state(
        state,
        status=receipt.status,
        tx_hash=receipt.tx_hash,
        block_number=receipt.block_number,
        gas_used=receipt.gas_used,
    )


def _result_from_state(
    state: MercuryState,
    *,
    status: ExecutionStatus,
    tx_hash: str | None = None,
    block_number: int | None = None,
    gas_used: int | None = None,
    error: MercuryErrorInfo | str | None = None,
) -> ExecutionResult:
    transaction = state.get("executable_transaction")
    prepared = state.get("prepared_transaction")
    chain = transaction.chain if isinstance(transaction, ExecutableTransaction) else "unknown"
    chain_id = transaction.chain_id if isinstance(transaction, ExecutableTransaction) else 1
    wallet_id = (
        transaction.wallet_id if isinstance(transaction, ExecutableTransaction) else "unknown"
    )
    if isinstance(prepared, PreparedTransaction):
        chain = prepared.chain
        wallet_id = prepared.wallet_id
        chain_id = prepared.chain_id or chain_id

    err_info: MercuryErrorInfo | None
    if error is None:
        err_info = None
    elif isinstance(error, MercuryErrorInfo):
        err_info = error
    else:
        err_info = internal_error(message=str(error), stage="execution_result")

    return ExecutionResult(
        chain=chain,
        chain_id=chain_id,
        wallet_id=wallet_id,
        wallet_address=state.get("wallet_address"),
        tx_hash=tx_hash,
        status=status,
        block_number=block_number,
        gas_used=gas_used,
        policy_decision=state.get("policy_decision"),
        error=err_info,
    )
