import pytest
from pydantic import ValidationError

from mercury.graph.state import MercuryState
from mercury.invoke import MercuryInvoker
from mercury.models.errors import internal_error
from mercury.service.errors import GraphInvocationError
from mercury.service.models import MercuryInvokeRequest


def test_invoke_validation_error_rejects_malformed_wallet_id() -> None:
    with pytest.raises(ValidationError) as ctx:
        MercuryInvokeRequest(
            request_id="req-validation",
            user_id="user-1",
            wallet_id="../primary",
            intent={"kind": "native_balance"},
        )
    assert "wallet_id" in str(ctx.value).lower()


def test_invoke_redacts_secret_like_graph_state_errors() -> None:
    runtime = StaticRuntime(
        {
            "chain_name": "ethereum",
            "error": internal_error(
                message=(
                    "failed using https://rpc.example.invalid and "
                    "mercury/wallets/primary/private_key"
                ),
            ),
        }
    )
    payload = MercuryInvokeRequest(
        request_id="req-redact",
        user_id="user-1",
        wallet_id="primary",
        intent={"kind": "native_balance"},
    )
    response = MercuryInvoker(runtime).invoke(payload)

    dumped = response.model_dump(mode="json")
    text = str(dumped)
    assert response.error is not None
    assert response.error.code == "internal_error"
    assert response.error.category == "internal"
    assert "https://rpc.example.invalid" not in text
    assert "mercury/wallets/primary/private_key" not in text
    assert "<redacted>" in text


def test_invoke_graph_exception_maps_to_sanitized_error() -> None:
    runtime = RaisingRuntime(RuntimeError("boom https://rpc.example.invalid bearer=secret-token"))
    payload = MercuryInvokeRequest(
        request_id="req-graph-error",
        user_id="user-1",
        wallet_id="primary",
        intent={"kind": "native_balance"},
    )
    with pytest.raises(GraphInvocationError) as ctx:
        MercuryInvoker(runtime).invoke(payload)
    text = str(ctx.value)
    assert "https://rpc.example.invalid" not in text
    assert "secret-token" not in text
    assert "<redacted>" in text


class StaticRuntime:
    def __init__(self, result: MercuryState) -> None:
        self._result = result

    def invoke(self, state: MercuryState) -> MercuryState:
        return self._result


class RaisingRuntime:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.called = False

    def invoke(self, state: MercuryState) -> MercuryState:
        self.called = True
        raise self._error
