"""Mercury specialist sub-agent for Juno (LangChain tool → runner)."""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain.agents import create_agent
from langchain.tools import InjectedState, tool
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import NotRequired

from juno.agents.remote_guide_middleware import build_remote_invoke_guide_middleware
from juno.agents.state import CustomAgentState

from mercury.juno.manifest import JunoAssistantManifest
from mercury.juno.runners import MercuryAssistantRunnerLike
from mercury.juno.tool_text import turn_result_to_tool_text


class MercuryJunoAgentState(CustomAgentState):
    """Juno agent state for the Mercury specialist, including resume correlation."""

    mercury_pending_request_id: NotRequired[str | None]


def _normalize_approval_response(raw: Any) -> dict[str, Any] | None:
    """Map graph state (Telegram string or dict) to Mercury ``approval_response`` object."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                parsed = json.loads(s)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        lower = s.lower()
        if lower.startswith("approved:"):
            rest = s.split(":", 1)[1].strip()
            out: dict[str, Any] = {
                "status": "approved",
                "reason": "telegram",
                "approved_by": "telegram_user",
            }
            if rest:
                out["idempotency_key"] = rest
            return out
        if lower.startswith("denied:"):
            rest = s.split(":", 1)[1].strip()
            out = {
                "status": "denied",
                "reason": "telegram",
                "approved_by": "telegram_user",
            }
            if rest:
                out["idempotency_key"] = rest
            return out
        return {"status": "approved", "reason": s, "approved_by": "telegram_user"}
    return {"status": "approved", "reason": str(raw), "approved_by": "telegram_user"}


def _sanitize_intent_for_mercury_post(intent: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    cleaned = dict(intent)
    cleaned.pop("approval_response", None)
    raw = cleaned.get("idempotency_key")
    idem: str | None = raw.strip() if isinstance(raw, str) and raw.strip() else None
    if idem is not None:
        cleaned["idempotency_key"] = idem
    return cleaned, idem


def _approval_dict_needs_idempotency(ap: dict[str, Any]) -> bool:
    raw = ap.get("idempotency_key")
    if raw is None:
        return True
    if isinstance(raw, str):
        return raw.strip() == ""
    return True


def _build_mercury_invoke_payload(
    state: dict[str, Any],
    intent: dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"intent": intent}
    uid = state.get("user_id")
    if uid is not None:
        payload["user_id"] = str(uid)
    wid = state.get("wallet_id")
    payload["wallet_id"] = str(wid) if wid else "primary"
    ch = state.get("chain")
    if ch is not None:
        payload["chain"] = str(ch)
    pending_rid = state.get("mercury_pending_request_id")
    if isinstance(pending_rid, str) and pending_rid.strip():
        payload["request_id"] = pending_rid.strip()
    ap = _normalize_approval_response(state.get("approval_response"))
    if ap is not None:
        ap_out = dict(ap)
        if idempotency_key and _approval_dict_needs_idempotency(ap_out):
            ap_out["idempotency_key"] = idempotency_key
        payload["approval_response"] = ap_out
    return payload


def build_mercury_juno_subagent(
    *,
    model: str | BaseChatModel,
    manifest: JunoAssistantManifest,
    runner: MercuryAssistantRunnerLike,
) -> CompiledStateGraph:
    """Build a LangChain agent whose tool forwards structured Mercury invokes."""
    parts: list[str] = []
    sys_prompt = manifest.system_prompt.strip()
    if sys_prompt:
        parts.append(sys_prompt)
    if manifest.instructions_md:
        md = manifest.instructions_md.strip()
        if md:
            parts.append(md)
    full_system = "\n\n".join(parts)

    @tool
    def mercury_invoke(
        intent_json: str,
        state: Annotated[dict[str, Any], InjectedState],
    ) -> str:
        """Invoke Mercury with a structured ``intent`` (in-process graph run).

        ``intent_json`` MUST be a JSON object with a ``kind`` field. Session fields
        ``user_id``, ``wallet_id``, ``chain``, ``mercury_pending_request_id`` (when
        resuming after ``approval_required``), and ``approval_response`` are merged
        from graph state (never nest ``approval_response`` under the intent).
        For value-moving calls include ``idempotency_key`` inside the intent.
        """

        try:
            intent = json.loads(intent_json)
        except json.JSONDecodeError as exc:
            return f"Invalid intent JSON: {exc}"
        if not isinstance(intent, dict):
            return "Intent must be a JSON object."
        if intent.get("kind") is None:
            return 'Intent must include a string "kind" field.'
        intent_clean, idempotency_key = _sanitize_intent_for_mercury_post(intent)
        payload = _build_mercury_invoke_payload(state, intent_clean, idempotency_key=idempotency_key)
        result = runner.run_turn(payload, idempotency_key=idempotency_key)
        return turn_result_to_tool_text(result)

    guide_path = (manifest.guide_path or "").strip()
    middleware: tuple[Any, ...] = ()
    if guide_path:
        middleware = (build_remote_invoke_guide_middleware(lambda: runner.fetch_get_text(guide_path)),)

    return create_agent(
        model=model,
        tools=[mercury_invoke],
        system_prompt=full_system,
        state_schema=MercuryJunoAgentState,
        middleware=middleware,
        checkpointer=None,
    )


__all__ = [
    "MercuryJunoAgentState",
    "build_mercury_juno_subagent",
    "_build_mercury_invoke_payload",
    "_sanitize_intent_for_mercury_post",
]
