"""Format :class:`~mercury.juno.assistant_turn.AssistantTurnResult` as tool-return text."""

from __future__ import annotations

import json

from juno.approval_markers import JUNO_WALLET_APPROVAL_UI_MARKER

from mercury.juno.assistant_turn import (
    AssistantTurnAgentError,
    AssistantTurnHttpError,
    AssistantTurnResult,
    AssistantTurnSuccess,
    AssistantTurnWalletApproval,
)


def turn_result_to_tool_text(result: AssistantTurnResult) -> str:
    """Format a Mercury turn result as plain text for the LLM."""
    if isinstance(result, AssistantTurnSuccess):
        if result.agent_reply:
            return result.agent_reply
        if result.task_result is not None:
            return json.dumps(result.task_result, default=str)
        return "Success (no reply or task_result)."
    if isinstance(result, AssistantTurnWalletApproval):
        idem = None
        if result.extras:
            idem = result.extras.get("idempotency_key")
        if not isinstance(idem, str):
            idem = result.approval_token or result.approval_id
        req_id = None
        if result.extras:
            rid = result.extras.get("request_id")
            if isinstance(rid, str) and rid.strip():
                req_id = rid.strip()
        parts = [
            "Wallet approval required (Mercury approval gate).",
            f"approval_token={result.approval_token!r}",
            f"approval_id={result.approval_id!r}",
        ]
        if isinstance(idem, str) and idem:
            parts.append(f'idempotency_key="{idem}"')
        if req_id:
            parts.append(f'request_id="{req_id}"')
        hint_parts: list[str] = []
        if result.extras:
            hint_parts.append(f"details={json.dumps(result.extras, default=str)}")
        hint_parts.append(
            "Save request_id: on the next mercury_invoke (after Telegram approve), session should "
            "carry mercury_pending_request_id equal to that value, with the same intent, same "
            "idempotency_key inside the intent, and top-level approval_response whose "
            "idempotency_key matches. Do not instruct MetaMask/hardware signing unless "
            "this deployment explicitly uses in-wallet signing; the default path is a second "
            "Mercury invoke with approval_response (1Claw-backed signer)."
        )
        parts.append(" ".join(hint_parts))
        return f"{JUNO_WALLET_APPROVAL_UI_MARKER}\n" + "\n".join(parts)
    if isinstance(result, AssistantTurnAgentError):
        line = f"Agent error: {result.message}"
        if result.code:
            line += f" (code={result.code})"
        if result.details:
            line += f" details={json.dumps(result.details, default=str)}"
        return line
    if isinstance(result, AssistantTurnHttpError):
        msg = result.message or f"HTTP {result.status_code}"
        return f"HTTP error {result.status_code}: {msg}\nBody (snippet): {result.body_snippet}"
    return f"Unknown result: {result!r}"


__all__ = ["turn_result_to_tool_text"]
