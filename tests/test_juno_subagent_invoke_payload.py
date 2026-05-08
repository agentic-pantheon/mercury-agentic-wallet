"""Sanitization / HTTP wiring for mercury_invoke (Mercury Juno specialist tool)."""

from __future__ import annotations

import json
import uuid

import httpx
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from mercury.juno.manifest import JunoAssistantManifest
from mercury.juno.runners import MercuryAssistantRunner
from mercury.juno.subagent import _sanitize_intent_for_mercury_post, build_mercury_juno_subagent


class _FakeWithTools(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        return self


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


def test_mercury_invoke_sends_root_idempotency_and_header() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = json.loads(request.content.decode())
        assert body.get("idempotency_key") == "transfer-usdc-1"
        assert body["intent"]["idempotency_key"] == "transfer-usdc-1"
        assert "approval_response" not in body["intent"]
        assert request.headers.get("Idempotency-Key") == "transfer-usdc-1"
        return httpx.Response(200, json={"agent_reply": "ok"})

    runner = MercuryAssistantRunner(
        "https://mercury.test",
        transport=httpx.MockTransport(handler),
        http_path="/v1/mercury/invoke",
        request_body_mode="flat",
    )
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
        manifest=JunoAssistantManifest(runner="mercury", base_url_env="X", system_prompt="S"),
        runner=runner,
    )
    sub.invoke({"messages": [HumanMessage("go")]}, _cfg())
    assert len(captured) == 1
