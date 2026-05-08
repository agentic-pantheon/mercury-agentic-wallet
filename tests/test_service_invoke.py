from mercury.graph.state import MercuryState
from mercury.invoke import MercuryInvoker
from mercury.models import ExecutionResult, ExecutionStatus
from mercury.models.approval import ApprovalResult, ApprovalStatus
from mercury.models.errors import approval_required
from mercury.service.models import MercuryInvokeRequest


def test_invoke_propagates_request_id_and_idempotency_key_to_runtime() -> None:
    runtime = CapturingRuntime(
        {
            "chain_name": "base",
            "response_text": "0x000000000000000000000000000000000000dEaD has 1 ETH.",
            "tool_result": {"balance": "1"},
        }
    )
    payload = MercuryInvokeRequest(
        user_id="user-1",
        wallet_id="primary",
        intent={
            "kind": "native_balance",
            "wallet_address": "0x000000000000000000000000000000000000dEaD",
        },
        chain="base",
    )
    response = MercuryInvoker(runtime).invoke(
        payload,
        x_request_id="req-header",
        idempotency_key="idem-header",
    )

    assert runtime.invocations == [
        {
            "request_id": "req-header",
            "raw_input": {
                "kind": "native_balance",
                "wallet_address": "0x000000000000000000000000000000000000dEaD",
                "chain": "base",
                "wallet_id": "primary",
                "idempotency_key": "idem-header",
                "metadata": {"user_id": "user-1"},
            },
        }
    ]
    assert response.request_id == "req-header"
    assert response.status == "succeeded"
    assert response.chain == "base"


def test_invoke_maps_approval_required_graph_result() -> None:
    execution = ExecutionResult(
        chain="base",
        chain_id=8453,
        wallet_id="primary",
        status=ExecutionStatus.APPROVAL_DENIED,
        error=approval_required(
            message="Human approval is required before signing idem-1.",
        ),
    )
    approval = ApprovalResult(
        status=ApprovalStatus.REQUIRED,
        reason="Human approval is required before signing idem-1.",
    )
    runtime = CapturingRuntime({"execution_result": execution, "approval_result": approval})
    payload = MercuryInvokeRequest(
        request_id="req-approval",
        user_id="user-1",
        wallet_id="primary",
        idempotency_key="idem-1",
        intent={"kind": "erc20_transfer", "chain": "base"},
    )
    response = MercuryInvoker(runtime).invoke(payload)

    assert response.status == "approval_required"
    assert response.approval_required is True
    assert response.approval_payload is not None
    assert response.approval_payload["status"] == "required"
    err = response.error
    assert err is not None
    assert err.message == "Human approval is required before signing idem-1."
    assert err.code == "approval_required"
    assert err.category == "approval"
    assert err.retryable is True
    assert err.recoverable is True
    assert err.user_action
    assert err.llm_action


def test_invoke_maps_transaction_success_result() -> None:
    execution = ExecutionResult(
        chain="base",
        chain_id=8453,
        wallet_id="primary",
        wallet_address="0x000000000000000000000000000000000000bEEF",
        tx_hash="0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        status=ExecutionStatus.CONFIRMED,
        block_number=123,
        gas_used=21_000,
    )
    runtime = CapturingRuntime({"execution_result": execution})
    payload = MercuryInvokeRequest(
        request_id="req-tx",
        user_id="user-1",
        wallet_id="primary",
        intent={"kind": "erc20_transfer", "chain": "base"},
    )
    response = MercuryInvoker(runtime).invoke(payload)

    assert response.status == "confirmed"
    assert response.tx_hash == execution.tx_hash
    assert response.receipt == {
        "tx_hash": execution.tx_hash,
        "status": "confirmed",
        "block_number": 123,
        "gas_used": 21000,
    }


class CapturingRuntime:
    def __init__(self, result: MercuryState) -> None:
        self._result = result
        self.invocations: list[MercuryState] = []

    def invoke(self, state: MercuryState) -> MercuryState:
        self.invocations.append(state)
        return self._result
