"""Mercury invoke JSON parsing and LocalMercuryAssistantRunner (Juno plugin)."""

import pytest
from juno.approval_markers import JUNO_WALLET_APPROVAL_UI_MARKER
from mercury.graph.state import MercuryState
from mercury.juno.assistant_turn import (
    AssistantTurnAgentError,
    AssistantTurnSuccess,
    AssistantTurnWalletApproval,
    parse_mercury_body,
)
from mercury.juno.runners import LocalMercuryAssistantRunner
from mercury.juno.tool_text import turn_result_to_tool_text

_MIN_INVOKE_PAYLOAD: dict[str, object] = {
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


def test_parse_approval_required_keeps_request_id_in_extras() -> None:
    r = parse_mercury_body(
        {
            "status": "approval_required",
            "request_id": "rid-42",
            "approval_required": True,
            "message": "approve me",
        },
    )
    assert isinstance(r, AssistantTurnWalletApproval)
    assert r.extras.get("request_id") == "rid-42"


def test_parse_approval_required_flattens_approval_payload_idempotency() -> None:
    r = parse_mercury_body(
        {
            "status": "approval_required",
            "request_id": "rid-42",
            "approval_required": True,
            "approval_payload": {"idempotency_key": "idem-42", "chain": "base"},
        },
    )
    assert isinstance(r, AssistantTurnWalletApproval)
    assert r.approval_token == "idem-42"
    assert r.extras.get("idempotency_key") == "idem-42"


def test_parse_invoke_response_becomes_task_result() -> None:
    r = parse_mercury_body({"native_balance": "1.23", "currency": "ETH"})
    assert isinstance(r, AssistantTurnSuccess)
    assert r.task_result is not None
    assert r.task_result.get("native_balance") == "1.23"


class _FakeRuntime:
    def __init__(self, state: MercuryState) -> None:
        self._state = state
        self.invocations: list[MercuryState] = []

    def invoke(self, state: MercuryState) -> MercuryState:
        self.invocations.append(state)
        return self._state


def test_local_run_turn_success_and_idempotency() -> None:
    fake = _FakeRuntime({"response_text": "ok", "chain_name": "base"})
    runner = LocalMercuryAssistantRunner(fake)  # type: ignore[arg-type]
    out = runner.run_turn(dict(_MIN_INVOKE_PAYLOAD), idempotency_key="idem-1")
    assert isinstance(out, AssistantTurnSuccess)
    assert len(fake.invocations) == 1
    raw = fake.invocations[0].get("raw_input")
    assert isinstance(raw, dict)
    assert raw.get("idempotency_key") == "idem-1"


def test_local_run_turn_body_idempotency_key_not_overwritten() -> None:
    fake = _FakeRuntime({"response_text": "x", "chain_name": "base"})
    runner = LocalMercuryAssistantRunner(fake)  # type: ignore[arg-type]
    runner.run_turn(
        {**_MIN_INVOKE_PAYLOAD, "idempotency_key": "from-body"},
        idempotency_key="from-arg",
    )
    raw = fake.invocations[0].get("raw_input")
    assert isinstance(raw, dict)
    assert raw.get("idempotency_key") == "from-body"


@pytest.mark.asyncio
async def test_local_arun_turn() -> None:
    fake = _FakeRuntime({"response_text": "async", "chain_name": "base", "tool_result": {"n": 2}})
    runner = LocalMercuryAssistantRunner(fake)  # type: ignore[arg-type]
    out = await runner.arun_turn(dict(_MIN_INVOKE_PAYLOAD))
    assert isinstance(out, AssistantTurnSuccess)


def test_turn_result_wallet_includes_structured_marker() -> None:
    text = turn_result_to_tool_text(
        AssistantTurnWalletApproval(approval_token="tok_1", approval_id="aid"),
    )
    assert text.startswith(f"{JUNO_WALLET_APPROVAL_UI_MARKER}\n")


def test_turn_result_wallet_includes_request_id_line() -> None:
    text = turn_result_to_tool_text(
        AssistantTurnWalletApproval(
            approval_token="tok_1",
            approval_id="aid",
            extras={"request_id": "rid-from-mercury"},
        ),
    )
    assert 'request_id="rid-from-mercury"' in text
    assert "mercury_pending_request_id" in text
