"""Local Mercury runner (used by Mercury's Juno plugin in MERCURY_RUNNER_MODE=local)."""

from __future__ import annotations

import pytest

from mercury.graph.state import MercuryState
from mercury.invoke import get_invoke_guide_markdown
from mercury.juno.assistant_turn import AssistantTurnAgentError, AssistantTurnSuccess
from mercury.juno.runners import LocalMercuryAssistantRunner
from mercury.models.errors import internal_error
from mercury.service.errors import GraphInvocationError


class _FakeRuntime:
    def __init__(self, state: MercuryState) -> None:
        self._state = state
        self.invocations: list[MercuryState] = []

    def invoke(self, state: MercuryState) -> MercuryState:
        self.invocations.append(state)
        return self._state


class _RaisingRuntime:
    def invoke(self, state: MercuryState) -> MercuryState:
        raise GraphInvocationError("simulated graph failure")


def _minimal_payload(**intent_overrides: object) -> dict[str, object]:
    intent: dict[str, object] = {
        "kind": "native_balance",
        "wallet_address": "0x1234567890123456789012345678901234567890",
    }
    intent.update(intent_overrides)
    return {
        "user_id": "user-1",
        "wallet_id": "primary",
        "intent": intent,
        "chain": "base",
    }


def test_local_run_turn_validation_error() -> None:
    runner = LocalMercuryAssistantRunner(_FakeRuntime({}))  # type: ignore[arg-type]
    out = runner.run_turn({})
    assert isinstance(out, AssistantTurnAgentError)
    assert out.code == "validation_error"
    assert out.details is not None
    assert "errors" in out.details


def test_local_run_turn_graph_invocation_error() -> None:
    runner = LocalMercuryAssistantRunner(_RaisingRuntime())  # type: ignore[arg-type]
    out = runner.run_turn(_minimal_payload())
    assert isinstance(out, AssistantTurnAgentError)
    assert out.code == GraphInvocationError.error_code
    assert "simulated graph failure" in out.message


def test_local_run_turn_success_via_parse() -> None:
    returned: MercuryState = {"response_text": "Balances loaded.", "chain_name": "base"}
    fake = _FakeRuntime(returned)
    runner = LocalMercuryAssistantRunner(fake)  # type: ignore[arg-type]
    out = runner.run_turn(_minimal_payload())
    assert isinstance(out, AssistantTurnSuccess)
    assert out.task_result is not None
    assert fake.invocations


def test_local_run_turn_native_error_maps_agent_error() -> None:
    returned: MercuryState = {
        "chain_name": "base",
        "error": internal_error(message="graph domain failure"),
    }
    runner = LocalMercuryAssistantRunner(_FakeRuntime(returned))  # type: ignore[arg-type]
    out = runner.run_turn(_minimal_payload())
    assert isinstance(out, AssistantTurnAgentError)
    assert out.code == "internal_error"
    assert out.message == "graph domain failure"


def test_local_run_turn_idempotency_key_header_semantics() -> None:
    returned: MercuryState = {"response_text": "ok"}
    fake = _FakeRuntime(returned)
    runner = LocalMercuryAssistantRunner(fake)  # type: ignore[arg-type]
    runner.run_turn(_minimal_payload(), idempotency_key="idem-from-header")
    assert len(fake.invocations) == 1
    raw = fake.invocations[0].get("raw_input")
    assert isinstance(raw, dict)
    assert raw.get("idempotency_key") == "idem-from-header"


def test_local_run_turn_body_idempotency_not_overwritten() -> None:
    returned: MercuryState = {"response_text": "ok"}
    fake = _FakeRuntime(returned)
    runner = LocalMercuryAssistantRunner(fake)  # type: ignore[arg-type]
    runner.run_turn(_minimal_payload(idempotency_key="from-body"), idempotency_key="from-arg")
    raw = fake.invocations[0].get("raw_input")
    assert isinstance(raw, dict)
    assert raw.get("idempotency_key") == "from-body"


def test_local_run_turn_request_id_from_body_is_graph_state_request_id() -> None:
    returned: MercuryState = {"response_text": "ok"}
    fake = _FakeRuntime(returned)
    runner = LocalMercuryAssistantRunner(fake)  # type: ignore[arg-type]
    pl = dict(_minimal_payload())
    pl["request_id"] = "body-rid-99"
    runner.run_turn(pl)
    assert len(fake.invocations) == 1
    assert fake.invocations[0].get("request_id") == "body-rid-99"


def test_fetch_get_text_invoke_guide() -> None:
    runner = LocalMercuryAssistantRunner(_FakeRuntime({}))  # type: ignore[arg-type]
    md = runner.fetch_get_text("/mercury/invoke-guide")
    assert md == get_invoke_guide_markdown()


def test_fetch_get_text_unsupported_path_is_deterministic() -> None:
    runner = LocalMercuryAssistantRunner(_FakeRuntime({}))  # type: ignore[arg-type]
    assert runner.fetch_get_text("/v1/other") == "(Local guide unavailable: unsupported path /v1/other)"
    assert runner.fetch_get_text("mercury/invoke-guide") == get_invoke_guide_markdown()


@pytest.mark.asyncio
async def test_local_arun_turn() -> None:
    fake = _FakeRuntime({"response_text": "async-ok"})
    runner = LocalMercuryAssistantRunner(fake)  # type: ignore[arg-type]
    out = await runner.arun_turn(_minimal_payload())
    assert isinstance(out, AssistantTurnSuccess)
