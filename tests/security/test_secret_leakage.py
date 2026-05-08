from __future__ import annotations

import json
import logging
from typing import Any

from fastapi.testclient import TestClient
from mercury.graph.agent import build_transaction_graph
from mercury.graph.nodes_transaction import TransactionGraphDependencies
from mercury.graph.state import MercuryState
from mercury.models import ExecutionStatus, GasFees, PreparedTransaction, SignedTransactionResult
from mercury.models.execution import ExecutableTransaction
from mercury.models.simulation import SimulationResult, SimulationStatus
from mercury.service import create_app
from mercury.service.logging import log_service_event

from tests.fakes.secret_store import TEST_ONECLAW_API_KEY, TEST_PRIVATE_KEY, TEST_RPC_URL
from tests.fakes.signer import RecordingSigner
from tests.fakes.transactions import RecordingApprover, RecordingTransactionBackend

WALLET = "0x000000000000000000000000000000000000bEEF"
RECIPIENT = "0x000000000000000000000000000000000000dEaD"
SECRET_VALUES = (
    TEST_PRIVATE_KEY,
    TEST_RPC_URL,
    TEST_ONECLAW_API_KEY,
    "mercury/wallets/primary/private_key",
    "rpc-token-test-only",
)


def test_transaction_graph_does_not_serialize_signer_private_key_or_secret_errors() -> None:
    events: list[str] = []
    signer = SecretHoldingSigner(events)
    graph = build_transaction_graph(
        TransactionGraphDependencies(
            backend=SecretFailingBackend(events),
            signer=signer,
            approver=RecordingApprover(events),
        )
    ).compile()

    result = graph.invoke({"raw_input": _prepared_transaction()})

    assert result["execution_result"].status == ExecutionStatus.REJECTED
    serialized = json.dumps(_jsonable(result), default=str, sort_keys=True)
    assert_no_secret_values(serialized)
    assert signer.sign_calls == 0
    assert "sign" not in events


def test_native_service_redacts_secrets_from_response_payload_and_logs(caplog: Any) -> None:
    runtime = StaticRuntime(
        {
            "chain_name": "base",
            "response_text": f"read via {TEST_RPC_URL}",
            "tool_result": {
                "rpc_url": TEST_RPC_URL,
                "metadata": {"api_key": TEST_ONECLAW_API_KEY},
                "note": "bearer=rpc-token-test-only",
            },
        }
    )
    client = TestClient(create_app(runtime=runtime))

    with caplog.at_level(logging.INFO, logger="mercury.service"):
        response = client.post(
            "/v1/mercury/invoke",
            json={
                "request_id": "req-secret-redaction",
                "user_id": "user-1",
                "wallet_id": "primary",
                "intent": {"kind": "native_balance"},
            },
        )
        log_service_event(
            "test_secret_event",
            rpc_url=TEST_RPC_URL,
            authorization=f"Bearer {TEST_ONECLAW_API_KEY}",
        )

    assert response.status_code == 200
    assert_no_secret_values(response.text)
    assert "<redacted>" in response.text
    assert_no_secret_values(caplog.text)


def test_native_invoke_route_redacts_malicious_payload_and_graph_errors() -> None:
    runtime = RaisingRuntime(
        RuntimeError(
            f"boom {TEST_RPC_URL} bearer={TEST_ONECLAW_API_KEY} mercury/wallets/primary/private_key"
        )
    )
    client = TestClient(create_app(runtime=runtime), raise_server_exceptions=False)

    response = client.post(
        "/v1/mercury/invoke",
        json={
            "request_id": "req-graph-error-redaction",
            "user_id": "user-1",
            "wallet_id": "primary",
            "chain": "base",
            "intent": {"kind": "native_balance"},
            "metadata": {
                "api_key": TEST_ONECLAW_API_KEY,
                "debug": f"using {TEST_RPC_URL}",
            },
        },
    )

    assert response.status_code == 500
    assert_no_secret_values(response.text)
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "graph_invocation_failed"
    assert payload["error"]["category"] == "internal"
    assert payload["error"]["llm_action"]
    assert_no_secret_values(payload["error"]["llm_action"])
    assert payload["error"]["message"].count("<redacted>") >= 2


def assert_no_secret_values(text: str) -> None:
    for secret in SECRET_VALUES:
        assert secret not in text


class SecretHoldingSigner(RecordingSigner):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events, wallet_address=WALLET)
        self.private_key = TEST_PRIVATE_KEY


class SecretFailingBackend(RecordingTransactionBackend):
    def populate_gas(self, transaction: PreparedTransaction | ExecutableTransaction) -> GasFees:
        self.events.append("gas")
        raise RuntimeError(f"gas failed via {TEST_RPC_URL} bearer={TEST_ONECLAW_API_KEY}")

    def simulate(self, transaction: ExecutableTransaction) -> SimulationResult:
        return SimulationResult(status=SimulationStatus.PASSED, gas_estimate=21_000)

    def broadcast(self, signed_transaction: SignedTransactionResult) -> str:
        raise AssertionError("broadcast must not be reached")


class StaticRuntime:
    def __init__(self, result: MercuryState) -> None:
        self._result = result

    def invoke(self, state: MercuryState) -> MercuryState:
        return self._result


class RaisingRuntime:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def invoke(self, state: MercuryState) -> MercuryState:
        raise self._error


def _prepared_transaction() -> dict[str, Any]:
    return {
        "wallet_id": "primary",
        "chain": "ethereum",
        "chain_id": 1,
        "to": RECIPIENT,
        "value_wei": 1,
        "data": "0x",
        "idempotency_key": "secret-leakage-1",
    }


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return value
