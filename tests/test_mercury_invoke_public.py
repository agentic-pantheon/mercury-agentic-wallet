"""Tests for public in-process Mercury invoke (`mercury.invoke`)."""

from __future__ import annotations

import json

import pytest
from mercury.graph.state import MercuryState
from mercury.invoke import MercuryInvoker, get_invoke_guide_markdown, invoke_mercury, invoke_response_from_state
from mercury.models.addresses import InvalidEVMAddressError
from mercury.models.errors import internal_error, validation_failed
from mercury.service.errors import GraphInvocationError
from mercury.service.models import MercuryInvokeRequest


def test_mercury_invoker_propagates_request_id_and_idempotency_to_runtime() -> None:
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


def test_invoke_mercury_module_function_delegates_to_runtime() -> None:
    runtime = CapturingRuntime(
        {
            "chain_name": "base",
            "response_text": "ok",
        }
    )
    payload = MercuryInvokeRequest(
        user_id="u",
        wallet_id="primary",
        intent={"kind": "native_balance"},
        chain="base",
    )
    response = invoke_mercury(runtime, payload)
    assert response.status == "succeeded"
    assert len(runtime.invocations) == 1


def test_mercury_invoker_raises_graph_invocation_error_with_redaction() -> None:
    runtime = RaisingRuntime(
        RuntimeError("boom https://rpc.example.invalid bearer=secret-token")
    )
    payload = MercuryInvokeRequest(
        user_id="user-1",
        wallet_id="primary",
        intent={"kind": "native_balance"},
    )
    with pytest.raises(GraphInvocationError) as ctx:
        MercuryInvoker(runtime).invoke(payload)
    assert "https://rpc.example.invalid" not in str(ctx.value)
    assert "secret-token" not in str(ctx.value)
    assert "<redacted>" in str(ctx.value)


def test_mercury_invoker_maps_state_error_with_redaction() -> None:
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
        user_id="user-1",
        wallet_id="primary",
        intent={"kind": "native_balance"},
    )
    response = MercuryInvoker(runtime).invoke(payload)
    assert response.status == "failed"
    assert response.error is not None
    assert response.error.code == "internal_error"
    dumped = response.model_dump(mode="json")
    text = str(dumped)
    assert "https://rpc.example.invalid" not in text
    assert "mercury/wallets/primary/private_key" not in text
    assert "<redacted>" in text


def test_invoke_response_serializes_validation_details_with_embedded_exceptions() -> None:
    """Pydantic validation errors may carry exception instances in ``ctx``; response must stay JSON-safe."""

    info = validation_failed(
        message="Invalid field 'from_token': invalid.",
        details={
            "errors": [
                {
                    "type": "value_error",
                    "loc": ("from_token",),
                    "msg": "Invalid EVM address.",
                    "ctx": {"error": InvalidEVMAddressError("Invalid EVM address.")},
                }
            ]
        },
    )
    state: MercuryState = {
        "request_id": "rid",
        "error": info,
        "chain_name": "ethereum",
    }
    response = invoke_response_from_state(state, request_id="rid", fallback_chain=None)
    assert response.status == "failed"
    assert response.error is not None
    dumped = response.model_dump(mode="json")

    json.dumps(dumped)
    assert "Invalid EVM address" in str(dumped)


def test_get_invoke_guide_markdown_matches_http_guide_contract() -> None:
    body = get_invoke_guide_markdown()
    assert "POST /v1/mercury/invoke" in body
    assert len(body) > 200


class CapturingRuntime:
    def __init__(self, result: MercuryState) -> None:
        self._result = result
        self.invocations: list[MercuryState] = []

    def invoke(self, state: MercuryState) -> MercuryState:
        self.invocations.append(state)
        return self._result


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
