"""Sanitization and local-runner wiring for mercury_invoke (Mercury Juno specialist tool)."""

from __future__ import annotations

import json
import uuid

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from mercury.graph.state import MercuryState
from mercury.juno.manifest import JunoAssistantManifest
from mercury.juno.runners import LocalMercuryAssistantRunner
from mercury.juno.subagent import _sanitize_intent_for_mercury_post, build_mercury_juno_subagent


class _FakeWithTools(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        return self


class _CapturingRuntime:
    def __init__(self) -> None:
        self.invocations: list[MercuryState] = []

    def invoke(self, state: MercuryState) -> MercuryState:
        self.invocations.append(state)
        return {"response_text": "ok", "chain_name": "base"}


def _cfg() -> dict:
    return {"configurable": {"thread_id": f"test-{uuid.uuid4()}"}}


def test_sanitize_strips_nested_approval_normalizes_idempotency() -> None:
    cleaned, idem = _sanitize_intent_for_mercury_post(
        {
            "kind": "erc20_transfer",
            "chain": "base",
            "idempotency_key": "  tx-1  ",
            "approval_response": {"status": "approved", "reason": "wrong place"},
        },
    )
    assert "approval_response" not in cleaned
    assert idem == "tx-1"
    assert cleaned["idempotency_key"] == "tx-1"


def test_sanitize_no_idempotency() -> None:
    cleaned, idem = _sanitize_intent_for_mercury_post({"kind": "native_balance", "wallet_address": "0xabc"})
    assert idem is None
    assert "idempotency_key" not in cleaned


def test_mercury_invoke_merges_idempotency_and_strips_nested_approval() -> None:
    rt = _CapturingRuntime()
    runner = LocalMercuryAssistantRunner(rt)  # type: ignore[arg-type]
    intent = json.dumps(
        {
            "kind": "erc20_transfer",
            "chain": "base",
            "idempotency_key": "transfer-usdc-1",
            "approval_response": {"status": "approved"},
        },
    )
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "mercury_invoke",
                    "args": {"intent_json": intent},
                    "id": "c1",
                    "type": "tool_call",
                },
            ],
        ),
        AIMessage(content="done"),
    ]
    model = _FakeWithTools(responses=responses)
    sub = build_mercury_juno_subagent(
        model=model,
        manifest=JunoAssistantManifest(runner="mercury", system_prompt="S"),
        runner=runner,
    )
    sub.invoke(
        {
            "messages": [HumanMessage("go")],
            "user_id": "user-1",
            "wallet_id": "primary",
            "chain": "base",
        },
        _cfg(),
    )
    assert len(rt.invocations) == 1
    state = rt.invocations[0]
    raw = state.get("raw_input")
    assert isinstance(raw, dict)
    assert raw.get("kind") == "erc20_transfer"
    assert raw.get("idempotency_key") == "transfer-usdc-1"
    assert "approval_response" not in raw
    md = raw.get("metadata")
    assert isinstance(md, dict)
    assert md.get("user_id") == "user-1"
