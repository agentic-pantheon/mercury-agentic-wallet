"""Mercury invoke JSON parsing and HTTP runner used by Juno's Mercury plugin."""

import json

import httpx
import pytest

from juno.approval_markers import JUNO_WALLET_APPROVAL_UI_MARKER

from mercury.juno.assistant_turn import (
    AssistantTurnAgentError,
    AssistantTurnHttpError,
    AssistantTurnSuccess,
    AssistantTurnWalletApproval,
    parse_mercury_body,
)
from mercury.juno.runners import MercuryAssistantRunner
from mercury.juno.tool_text import turn_result_to_tool_text

_MIN_HTTP_INVOKE_PAYLOAD: dict[str, object] = {
    "user_id": "u1",
    "wallet_id": "primary",
    "chain": "base",
    "intent": {"kind": "native_balance", "wallet_address": "0xabc"},
}


def test_parse_success_flat() -> None:
    r = parse_mercury_body({"agent_reply": "Hello"})
    assert isinstance(r, AssistantTurnSuccess)
    assert r.agent_reply == "Hello"
    assert r.task_result is None


def test_parse_success_nested_data() -> None:
    r = parse_mercury_body({"data": {"agent_reply": "Nested", "task_result": {"x": 1}}})
    assert isinstance(r, AssistantTurnSuccess)
    assert r.agent_reply == "Nested"
    assert r.task_result == {"x": 1}


def test_parse_wallet_bool() -> None:
    r = parse_mercury_body(
        {"wallet_approval_required": True, "approval_token": "tok_1", "chain": "base"},
    )
    assert isinstance(r, AssistantTurnWalletApproval)
    assert r.approval_token == "tok_1"
    assert r.extras.get("chain") == "base"


def test_parse_wallet_dict() -> None:
    r = parse_mercury_body(
        {
            "wallet_approval_required": {
                "token": "t",
                "expires_at": "2026-01-01T00:00:00Z",
            },
        },
    )
    assert isinstance(r, AssistantTurnWalletApproval)
    assert r.approval_token == "t"
    assert r.extras.get("expires_at") == "2026-01-01T00:00:00Z"


def test_parse_agent_error_dict() -> None:
    r = parse_mercury_body({"agent_error": {"message": "bad", "code": "e1"}})
    assert isinstance(r, AssistantTurnAgentError)
    assert r.message == "bad"
    assert r.code == "e1"


def test_parse_agent_error_str() -> None:
    r = parse_mercury_body({"agent_error": "oops"})
    assert isinstance(r, AssistantTurnAgentError)
    assert r.message == "oops"


def test_parse_empty_wallet_dict_is_success() -> None:
    r = parse_mercury_body({"wallet_approval_required": {}})
    assert isinstance(r, AssistantTurnSuccess)


def test_parse_approval_required_status() -> None:
    r = parse_mercury_body({"status": "approval_required", "idempotency_key": "swap-1"})
    assert isinstance(r, AssistantTurnWalletApproval)
    assert r.approval_token == "swap-1"


def test_parse_invoke_response_becomes_task_result() -> None:
    r = parse_mercury_body({"native_balance": "1.23", "currency": "ETH"})
    assert isinstance(r, AssistantTurnSuccess)
    assert r.task_result is not None
    assert r.task_result.get("native_balance") == "1.23"


def test_run_turn_success_and_idempotency() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"agent_reply": "ok"})

    runner = MercuryAssistantRunner(
        "https://mercury.test",
        transport=httpx.MockTransport(handler),
        http_path="/v1/mercury/invoke",
        request_body_mode="flat",
    )
    out = runner.run_turn(dict(_MIN_HTTP_INVOKE_PAYLOAD), idempotency_key="idem-1")
    assert isinstance(out, AssistantTurnSuccess)
    assert out.agent_reply == "ok"
    assert len(captured) == 1
    req = captured[0]
    assert req.headers.get("Idempotency-Key") == "idem-1"
    assert b'"idempotency_key":"idem-1"' in (req.content or b"")


def test_run_turn_body_idempotency_key_not_overwritten() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"agent_reply": "x"})

    runner = MercuryAssistantRunner(
        "https://mercury.test",
        transport=httpx.MockTransport(handler),
        http_path="/v1/mercury/invoke",
        request_body_mode="flat",
    )
    runner.run_turn({**_MIN_HTTP_INVOKE_PAYLOAD, "idempotency_key": "from-body"}, idempotency_key="from-arg")
    payload = json.loads(captured[0].content.decode())
    assert payload["idempotency_key"] == "from-body"
    assert captured[0].headers.get("Idempotency-Key") == "from-arg"


def test_run_turn_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    runner = MercuryAssistantRunner(
        "https://mercury.test",
        transport=httpx.MockTransport(handler),
        http_path="/v1/mercury/invoke",
        request_body_mode="flat",
    )
    out = runner.run_turn(dict(_MIN_HTTP_INVOKE_PAYLOAD))
    assert isinstance(out, AssistantTurnHttpError)
    assert out.status_code == 502
    assert "bad gateway" in out.body_snippet


@pytest.mark.asyncio
async def test_arun_turn() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"task_result": {"n": 2}})

    runner = MercuryAssistantRunner(
        "https://mercury.test",
        transport=httpx.MockTransport(handler),
        http_path="/v1/mercury/invoke",
        request_body_mode="flat",
    )
    out = await runner.arun_turn(dict(_MIN_HTTP_INVOKE_PAYLOAD))
    assert isinstance(out, AssistantTurnSuccess)
    assert out.task_result == {"n": 2}


def test_run_turn_defaults_mercury_invoke_flat_and_x_request_id() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.url.path == "/v1/mercury/invoke"
        assert request.headers.get("X-Request-ID")
        body = json.loads(request.content.decode())
        assert body["intent"]["kind"] == "native_balance"
        assert body["wallet_id"] == "primary"
        return httpx.Response(200, json={"agent_reply": "invoked"})

    runner = MercuryAssistantRunner("https://mercury.test", transport=httpx.MockTransport(handler))
    out = runner.run_turn(
        {
            "user_id": "u1",
            "wallet_id": "primary",
            "chain": "base",
            "intent": {"kind": "native_balance", "wallet_address": "0xabc"},
        },
    )
    assert isinstance(out, AssistantTurnSuccess)
    assert out.agent_reply == "invoked"


def test_run_turn_nested_input_mode() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        outer = json.loads(request.content.decode())
        assert "input" in outer
        assert outer["input"]["intent"]["kind"] == "x"
        return httpx.Response(200, json={"agent_reply": "ok"})

    runner = MercuryAssistantRunner(
        "https://mercury.test",
        transport=httpx.MockTransport(handler),
        http_path="/v1/legacy",
        request_body_mode="nested_input",
    )
    out = runner.run_turn({"intent": {"kind": "x"}})
    assert isinstance(out, AssistantTurnSuccess)


def test_turn_result_wallet_includes_structured_marker() -> None:
    text = turn_result_to_tool_text(
        AssistantTurnWalletApproval(approval_token="tok_1", approval_id="aid"),
    )
    assert text.startswith(f"{JUNO_WALLET_APPROVAL_UI_MARKER}\n")
