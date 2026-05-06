from __future__ import annotations

from fastapi.testclient import TestClient
from mercury.graph.state import MercuryState
from mercury.models import ExecutionResult, ExecutionStatus
from mercury.models.approval import ApprovalResult, ApprovalStatus
from mercury.models.errors import approval_required
from mercury.service import create_app


def test_invoke_guide_returns_markdown() -> None:
    runtime = CapturingRuntime({})
    client = TestClient(create_app(runtime=runtime))

    response = client.get("/v1/mercury/invoke/guide")

    assert response.status_code == 200
    assert "text/markdown" in response.headers.get("content-type", "")
    assert response.text.startswith("# Mercury:")
    assert "`POST /v1/mercury/invoke`" in response.text
    assert "`token_address`" in response.text or "token_address" in response.text
    assert "ENS" in response.text
    assert "ENSIP" in response.text


def test_native_api_health_ready_and_readonly_invoke_routes() -> None:
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

    response = client.post(
        "/v1/mercury/invoke",
        headers={"X-Request-ID": "req-native", "Idempotency-Key": "idem-native"},
        json={
            "user_id": "user-1",
            "wallet_id": "primary",
            "chain": "base",
            "intent": {
                "kind": "native_balance",
                "wallet_address": "0x000000000000000000000000000000000000dEaD",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["request_id"] == "req-native"
    assert payload["status"] == "succeeded"
    assert payload["chain"] == "base"
    assert runtime.invocations[0]["raw_input"]["idempotency_key"] == "idem-native"
    assert runtime.invocations[0]["raw_input"]["metadata"]["user_id"] == "user-1"


def test_native_api_preserves_approval_required_shape() -> None:
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
    client = TestClient(
        create_app(
            runtime=CapturingRuntime({"execution_result": execution, "approval_result": approval})
        )
    )

    response = client.post(
        "/v1/mercury/invoke",
        json={
            "request_id": "req-approval",
            "user_id": "user-1",
            "wallet_id": "primary",
            "idempotency_key": "idem-approval",
            "intent": {"kind": "erc20_transfer", "chain": "base"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "approval_required"
    assert payload["approval_required"] is True
    assert payload["approval_payload"]["status"] == "required"
    err = payload["error"]
    assert err["code"] == "approval_required"
    assert err["category"] == "approval"
    assert err["message"] == "Human approval is required before signing idem-approval."


def test_native_api_sanitizes_runtime_exception() -> None:
    client = TestClient(
        create_app(
            runtime=RaisingRuntime(
                RuntimeError("boom https://rpc.example.invalid api_key=q9wz-leak-test")
            )
        ),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/v1/mercury/invoke",
        json={
            "request_id": "req-error",
            "user_id": "user-1",
            "wallet_id": "primary",
            "intent": {"kind": "native_balance"},
        },
    )

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "graph_invocation_failed"
    assert body["error"]["category"] == "internal"
    assert "https://rpc.example.invalid" not in response.text
    assert "q9wz-leak-test" not in response.text
    assert "<redacted>" in response.text


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
