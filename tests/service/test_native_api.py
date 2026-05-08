from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from mercury.graph.state import MercuryState
from mercury.invoke import MercuryInvoker, get_invoke_guide_markdown
from mercury.models import ExecutionResult, ExecutionStatus
from mercury.models.approval import ApprovalResult, ApprovalStatus
from mercury.models.errors import approval_required
from mercury.service import create_app
from mercury.service.errors import GraphInvocationError
from mercury.service.models import MercuryInvokeRequest


def test_invoke_guide_markdown_bundled() -> None:
    body = get_invoke_guide_markdown()
    assert body.startswith("# Mercury:")
    assert "MercuryInvokeRequest" in body
    assert "`token_address`" in body or "token_address" in body
    assert "ENS" in body
    assert "ENSIP" in body


def test_native_api_health_ready_only() -> None:
    runtime = CapturingRuntime(
        {
            "chain_name": "base",
            "response_text": "0x000000000000000000000000000000000000dEaD has 1 ETH.",
            "tool_result": {"balance": "1"},
        }
    )
    client = TestClient(create_app(runtime=runtime))

    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").json()["supported_chains"] == [
        "ethereum",
        "base",
        "arbitrum",
        "optimism",
        "monad",
    ]

    payload = MercuryInvokeRequest(
        user_id="user-1",
        wallet_id="primary",
        chain="base",
        intent={
            "kind": "native_balance",
            "wallet_address": "0x000000000000000000000000000000000000dEaD",
        },
    )
    response = MercuryInvoker(runtime).invoke(
        payload,
        x_request_id="req-native",
        idempotency_key="idem-native",
    )
    assert response.request_id == "req-native"
    assert response.status == "succeeded"
    assert response.chain == "base"
    assert runtime.invocations[0]["raw_input"]["idempotency_key"] == "idem-native"
    assert runtime.invocations[0]["raw_input"]["metadata"]["user_id"] == "user-1"


def test_native_api_preserves_approval_required_shape_via_invoker() -> None:
    execution = ExecutionResult(
        chain="base",
        chain_id=8453,
        wallet_id="primary",
        status=ExecutionStatus.APPROVAL_DENIED,
        error=approval_required(
            message="Human approval is required before signing idem-approval.",
        ),
    )
    approval = ApprovalResult(
        status=ApprovalStatus.REQUIRED,
        reason="Human approval is required before signing idem-approval.",
    )
    runtime = CapturingRuntime({"execution_result": execution, "approval_result": approval})
    payload = MercuryInvokeRequest(
        request_id="req-approval",
        user_id="user-1",
        wallet_id="primary",
        idempotency_key="idem-approval",
        intent={"kind": "erc20_transfer", "chain": "base"},
    )
    response = MercuryInvoker(runtime).invoke(payload)

    assert response.status == "approval_required"
    assert response.approval_required is True
    assert response.approval_payload is not None
    assert response.approval_payload["status"] == "required"
    err = response.error
    assert err is not None
    assert err.code == "approval_required"
    assert err.category == "approval"
    assert err.message == "Human approval is required before signing idem-approval."


def test_native_api_sanitizes_runtime_exception_via_invoker() -> None:
    runtime = RaisingRuntime(
        RuntimeError("boom https://rpc.example.invalid api_key=q9wz-leak-test")
    )
    payload = MercuryInvokeRequest(
        request_id="req-error",
        user_id="user-1",
        wallet_id="primary",
        intent={"kind": "native_balance"},
    )
    with pytest.raises(GraphInvocationError) as ctx:
        MercuryInvoker(runtime).invoke(payload)
    text = str(ctx.value)
    assert "https://rpc.example.invalid" not in text
    assert "q9wz-leak-test" not in text
    assert "<redacted>" in text


class CapturingRuntime:
    def __init__(self, result: MercuryState) -> None:
        self._result = result
        self.invocations: list[MercuryState] = []

    def invoke(self, state: MercuryState) -> MercuryState:
        self.invocations.append(state)
        return self._result


class RaisingRuntime:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def invoke(self, state: MercuryState) -> MercuryState:
        raise self._error

