"""Pydantic models for Mercury JSON invoke responses.

Mercury returns a JSON object. Shapes may be flat or nested under ``data``.
The runner merges top-level keys with ``data`` (later wins on conflict).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AssistantTurnSuccess(BaseModel):
    """Terminal success: optional user-facing text and/or structured task output."""

    kind: Literal["success"] = "success"
    agent_reply: str | None = None
    task_result: dict[str, Any] | None = None


class AssistantTurnWalletApproval(BaseModel):
    """Mercury indicates the user must approve a wallet action."""

    kind: Literal["wallet_approval_required"] = "wallet_approval_required"
    approval_token: str | None = None
    approval_id: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)


class AssistantTurnAgentError(BaseModel):
    """Error reported by the agent service in the JSON body."""

    kind: Literal["agent_error"] = "agent_error"
    message: str
    code: str | None = None
    details: dict[str, Any] | None = None


class AssistantTurnHttpError(BaseModel):
    """Non-success HTTP status from the Mercury endpoint."""

    kind: Literal["http_error"] = "http_error"
    status_code: int
    body_snippet: str
    message: str | None = None


type AssistantTurnResult = (
    AssistantTurnSuccess
    | AssistantTurnWalletApproval
    | AssistantTurnAgentError
    | AssistantTurnHttpError
)


def _merge_mercury_payload(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    data = out.get("data")
    if isinstance(data, dict):
        merged = {**out, **data}
        merged.pop("data", None)
        return merged
    return out


def parse_mercury_body(raw: dict[str, Any]) -> AssistantTurnResult:
    """Parse a Mercury JSON envelope into :class:`AssistantTurnResult`."""
    effective = _merge_mercury_payload(raw)

    st = effective.get("status")
    if st == "approval_required" or effective.get("approval_required") is True:
        extras = {k: v for k, v in effective.items() if k not in ("status", "approval_required")}
        approval_payload = effective.get("approval_payload")
        if isinstance(approval_payload, dict):
            payload_idem = approval_payload.get("idempotency_key")
            if isinstance(payload_idem, str) and payload_idem:
                extras.setdefault("idempotency_key", payload_idem)
        token = effective.get("approval_token") or effective.get("idempotency_key")
        if not isinstance(token, str):
            token = extras.get("idempotency_key")
        if not isinstance(token, str):
            token = None
        approval_id = effective.get("approval_id")
        return AssistantTurnWalletApproval(
            approval_token=token,
            approval_id=approval_id if isinstance(approval_id, str) else None,
            extras=extras,
        )

    wa = effective.get("wallet_approval_required")
    if wa is True:
        known = {"wallet_approval_required", "data"}
        extras = {k: v for k, v in effective.items() if k not in known}
        token = effective.get("approval_token")
        if token is None and isinstance(effective.get("token"), str):
            token = effective.get("token")
        aid = effective.get("approval_id")
        if aid is None and isinstance(effective.get("id"), str):
            aid = effective.get("id")
        return AssistantTurnWalletApproval(
            approval_token=token if isinstance(token, str) else None,
            approval_id=aid if isinstance(aid, str) else None,
            extras=extras,
        )
    if isinstance(wa, dict) and wa:
        extras = dict(wa)
        token = extras.pop("approval_token", None) or extras.pop("token", None)
        aid = extras.pop("approval_id", None) or extras.pop("id", None)
        if not isinstance(token, str):
            token = None
        if not isinstance(aid, str):
            aid = None
        return AssistantTurnWalletApproval(
            approval_token=token,
            approval_id=aid,
            extras=extras,
        )

    ae = effective.get("agent_error")
    if ae is not None:
        if isinstance(ae, str):
            return AssistantTurnAgentError(message=ae)
        if isinstance(ae, dict):
            msg = ae.get("message")
            if msg is None:
                msg = ae.get("error")
            if msg is None:
                msg = str(ae) if ae else "Unknown agent error"
            else:
                msg = str(msg)
            code = ae.get("code")
            code_s = str(code) if code is not None else None
            detail_keys = {"message", "error", "code"}
            details = {k: v for k, v in ae.items() if k not in detail_keys} or None
            return AssistantTurnAgentError(message=msg, code=code_s, details=details)
        return AssistantTurnAgentError(message=str(ae))

    agent_reply = effective.get("agent_reply")
    if agent_reply is not None and not isinstance(agent_reply, str):
        agent_reply = str(agent_reply)

    task_result = effective.get("task_result")
    if task_result is not None and not isinstance(task_result, dict):
        task_result = {"value": task_result}

    if agent_reply is None and task_result is None and effective:
        task_result = dict(effective)

    return AssistantTurnSuccess(
        agent_reply=agent_reply if isinstance(agent_reply, str) else None,
        task_result=task_result if isinstance(task_result, dict) else None,
    )


__all__ = [
    "AssistantTurnAgentError",
    "AssistantTurnHttpError",
    "AssistantTurnResult",
    "AssistantTurnSuccess",
    "AssistantTurnWalletApproval",
    "parse_mercury_body",
]
