"""pan-agentikit envelope adapter for Mercury's service boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import uuid4

from pydantic import ValidationError

from mercury.graph.runtime import GraphRuntime
from mercury.graph.state import MercuryState
from mercury.invoke import invoke_response_from_state
from mercury.service.logging import redact_error_message, redact_value
from mercury.service.models import MercuryInvokeRequest, MercuryInvokeResponse
from mercury.service.pan_agentikit_models import (
    AgentErrorV1,
    AgentReplyV1,
    PanAgentEnvelope,
    TaskRequestV1,
    TaskResultV1,
    UserMessageV1,
    WalletApprovalRequiredV1,
)

_MERCURY_ROLE = "mercury"
_PAYLOAD_KIND_ALIASES = {
    "UserMessageV1": "user_message",
    "user_message": "user_message",
    "TaskRequestV1": "task_request",
    "task_request": "task_request",
}
_SUPPORTED_PAYLOADS = {"user_message", "task_request"}
_VALUE_MOVING_KINDS = {"erc20_transfer", "erc20_approval", "native_transfer", "swap"}


def _adapter_error_category(code: str) -> str:
    if code in {"invalid_payload", "invalid_mercury_request"}:
        return "validation"
    if code == "missing_idempotency_key":
        return "policy"
    if code == "unsupported_payload":
        return "intent"
    return "internal"


def handle_agent_envelope(
    envelope: PanAgentEnvelope,
    *,
    graph_runtime: GraphRuntime,
    request_id: str | None = None,
    idempotency_key: str | None = None,
) -> PanAgentEnvelope:
    """Parse an inbound envelope, invoke Mercury, and return an outbound envelope."""

    try:
        mercury_request = mercury_request_from_envelope(
            envelope,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
    except AdapterError as exc:
        return error_envelope(
            envelope,
            code=exc.code,
            message=str(exc),
            category=_adapter_error_category(exc.code),
            details=exc.details,
        )
    except ValidationError as exc:
        return error_envelope(
            envelope,
            code="invalid_mercury_request",
            message="Envelope payload could not be mapped to a valid Mercury request.",
            category="validation",
            retryable=False,
            llm_action="Inspect validation details, correct the envelope, and retry.",
            details={"errors": redact_value(exc.errors())},
        )

    effective_request_id = mercury_request.effective_request_id(request_id)
    effective_idempotency_key = mercury_request.effective_idempotency_key(idempotency_key)
    state = _state_from_mercury_request(
        mercury_request,
        request_id=effective_request_id,
        idempotency_key=effective_idempotency_key,
    )

    try:
        result_state = graph_runtime.invoke(state)
    except Exception as exc:  # pragma: no cover - exercised through route tests
        return error_envelope(
            envelope,
            code="graph_invocation_failed",
            message=redact_error_message(exc),
            category="internal",
            retryable=True,
            llm_action=(
                "Retry once with backoff. If it still fails, stop and report an internal error "
                "without exposing raw errors or secrets."
            ),
            metadata=_response_metadata(mercury_request, effective_idempotency_key),
        )

    native_response = _native_response_from_state(
        result_state,
        request_id=effective_request_id,
        fallback_chain=mercury_request.chain,
    )
    return envelope_from_mercury_response(
        envelope,
        mercury_request=mercury_request,
        native_response=native_response,
        idempotency_key=effective_idempotency_key,
    )


def mercury_request_from_envelope(
    envelope: PanAgentEnvelope,
    *,
    request_id: str | None = None,
    idempotency_key: str | None = None,
) -> MercuryInvokeRequest:
    """Map supported pan-agentikit payloads into Mercury's native request model."""

    payload_kind = _canonical_payload_kind(envelope.payload_kind)
    if payload_kind not in _SUPPORTED_PAYLOADS:
        raise AdapterError(
            f"Unsupported pan-agentikit payload: {payload_kind or 'unknown'}.",
            code="unsupported_payload",
            details={"payload_kind": payload_kind},
        )

    if payload_kind == "user_message":
        user_payload = UserMessageV1.model_validate(envelope.payload)
        return _request_from_user_message(
            envelope,
            user_payload,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    task_payload = TaskRequestV1.model_validate(envelope.payload)
    return _request_from_task(
        envelope,
        task_payload,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


def envelope_from_mercury_response(
    inbound: PanAgentEnvelope,
    *,
    mercury_request: MercuryInvokeRequest,
    native_response: MercuryInvokeResponse,
    idempotency_key: str | None,
) -> PanAgentEnvelope:
    """Map a native Mercury response into a pan-agentikit-compatible envelope."""

    metadata = _response_metadata(mercury_request, idempotency_key)
    if native_response.approval_required and native_response.approval_payload is not None:
        payload = WalletApprovalRequiredV1(
            task_id=_task_id(mercury_request),
            message=native_response.message,
            approval=native_response.approval_payload,
            idempotency_key=idempotency_key,
            metadata=metadata,
        ).model_dump(mode="json")
        return _reply_envelope(inbound, payload=payload, metadata=metadata)

    if native_response.error is not None or native_response.status in {"failed", "rejected"}:
        err = native_response.error
        if err is not None:
            detail_parts: dict[str, Any] = {}
            if err.details:
                detail_parts["error_details"] = dict(err.details)
            invoke_snap = _result_payload(native_response)
            if invoke_snap:
                detail_parts["invoke"] = invoke_snap
            return error_envelope(
                inbound,
                code=err.code,
                message=err.message,
                category=err.category,
                retryable=err.retryable,
                recoverable=err.recoverable,
                user_action=err.user_action,
                llm_action=err.llm_action,
                details=detail_parts or None,
                metadata=metadata,
            )
        return error_envelope(
            inbound,
            code="mercury_error",
            message=native_response.message,
            category="internal",
            details=_result_payload(native_response),
            metadata=metadata,
        )

    if _canonical_payload_kind(inbound.payload_kind) == "task_request":
        payload = TaskResultV1(
            task_id=_task_id(mercury_request),
            status=native_response.status,
            text=native_response.message,
            message=native_response.message,
            result=_result_payload(native_response),
            artifacts=inbound.artifacts,
            idempotency_key=idempotency_key,
            metadata=metadata,
        ).model_dump(mode="json")
        return _reply_envelope(inbound, payload=payload, metadata=metadata)

    payload = AgentReplyV1(
        text=native_response.message,
        content=native_response.message,
        status=native_response.status,
        result=_result_payload(native_response),
        artifacts=inbound.artifacts,
        metadata=metadata,
    ).model_dump(mode="json")
    return _reply_envelope(inbound, payload=payload, metadata=metadata)


def error_envelope(
    inbound: PanAgentEnvelope,
    *,
    code: str,
    message: str,
    category: str = "internal",
    retryable: bool = False,
    recoverable: bool = True,
    user_action: str | None = None,
    llm_action: str | None = None,
    details: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> PanAgentEnvelope:
    """Return a sanitized pan-agentikit error envelope."""

    safe_message = redact_error_message(message)
    raw_details = redact_value(details) if details is not None else None
    if raw_details is not None and not isinstance(raw_details, dict):
        safe_details: dict[str, Any] | None = {"value": raw_details}
    else:
        safe_details = raw_details
    safe_user = redact_error_message(user_action) if user_action else None
    safe_llm = redact_error_message(llm_action) if llm_action else None
    payload = AgentErrorV1(
        code=code,
        category=category,
        message=safe_message,
        retryable=retryable,
        recoverable=recoverable,
        user_action=safe_user,
        llm_action=safe_llm,
        details=safe_details,
        metadata=metadata or {},
    ).model_dump(mode="json")
    error_body: dict[str, Any] = {
        "code": code,
        "category": category,
        "message": safe_message,
        "retryable": retryable,
        "recoverable": recoverable,
        "user_action": safe_user,
        "llm_action": safe_llm,
        "details": safe_details if safe_details is not None else {},
    }
    return _reply_envelope(
        inbound,
        payload=payload,
        error=error_body,
        metadata=metadata or {},
    )


class AdapterError(ValueError):
    """Safe adapter error returned as an envelope instead of raising through FastAPI."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _request_from_user_message(
    envelope: PanAgentEnvelope,
    payload: UserMessageV1,
    *,
    request_id: str | None,
    idempotency_key: str | None,
) -> MercuryInvokeRequest:
    content = payload.effective_content()
    if not content:
        raise AdapterError("UserMessageV1 payload must include content.", code="invalid_payload")

    effective_idempotency_key = _effective_idempotency_key(envelope, payload, idempotency_key)
    metadata = _inbound_metadata(envelope, payload.metadata)
    return MercuryInvokeRequest(
        request_id=_effective_request_id(envelope, request_id),
        user_id=_required_identity("user_id", envelope, payload.user_id),
        wallet_id=_required_identity("wallet_id", envelope, payload.wallet_id),
        intent=content,
        chain=payload.chain or _string_metadata(envelope, "chain"),
        idempotency_key=effective_idempotency_key,
        metadata=metadata,
    )


def _request_from_task(
    envelope: PanAgentEnvelope,
    payload: TaskRequestV1,
    *,
    request_id: str | None,
    idempotency_key: str | None,
) -> MercuryInvokeRequest:
    intent = _task_intent(payload)
    effective_idempotency_key = _effective_idempotency_key(envelope, payload, idempotency_key)
    if _is_value_moving_intent(intent) and effective_idempotency_key is None:
        raise AdapterError(
            "Value-moving wallet tasks require an idempotency key.",
            code="missing_idempotency_key",
            details={"task_id": payload.task_id},
        )

    metadata = _inbound_metadata(envelope, payload.metadata)
    if payload.task_id is not None:
        metadata["task_id"] = payload.task_id
    if payload.task_type is not None:
        metadata["task_type"] = payload.task_type
    if payload.name is not None:
        metadata["task_name"] = payload.name
    if payload.artifacts:
        metadata["payload_artifacts"] = payload.artifacts

    return MercuryInvokeRequest(
        request_id=_effective_request_id(envelope, request_id),
        user_id=_required_identity("user_id", envelope, payload.user_id),
        wallet_id=_required_identity("wallet_id", envelope, payload.wallet_id),
        intent=intent,
        chain=payload.chain or _string_metadata(envelope, "chain"),
        idempotency_key=effective_idempotency_key,
        metadata=metadata,
    )


def _state_from_mercury_request(
    payload: MercuryInvokeRequest,
    *,
    request_id: str,
    idempotency_key: str | None,
) -> MercuryState:
    raw_input = _intent_with_boundary_fields(payload, idempotency_key=idempotency_key)
    return {"request_id": request_id, "raw_input": raw_input}


def _intent_with_boundary_fields(
    payload: MercuryInvokeRequest,
    *,
    idempotency_key: str | None,
) -> dict[str, Any] | str:
    if not isinstance(payload.intent, dict):
        return payload.intent

    intent = dict(payload.intent)
    if payload.chain is not None:
        intent.setdefault("chain", payload.chain)
    intent.setdefault("wallet_id", payload.wallet_id)
    if idempotency_key is not None:
        intent.setdefault("idempotency_key", idempotency_key)

    metadata = dict(payload.metadata)
    if payload.approval_response is not None:
        metadata["approval_response"] = payload.approval_response
    metadata["user_id"] = payload.user_id
    if metadata:
        intent.setdefault("metadata", metadata)
    return intent


def _native_response_from_state(
    state: MercuryState,
    *,
    request_id: str,
    fallback_chain: str | None,
) -> MercuryInvokeResponse:
    return invoke_response_from_state(
        state, request_id=request_id, fallback_chain=fallback_chain
    )


def _task_intent(payload: TaskRequestV1) -> dict[str, Any] | str:
    if payload.intent is not None:
        return payload.intent
    if isinstance(payload.input, Mapping):
        nested_intent = payload.input.get("intent")
        if isinstance(nested_intent, dict | str):
            return nested_intent
        if _looks_like_intent(payload.input):
            return dict(payload.input)
    if isinstance(payload.input, str):
        return payload.input
    if isinstance(payload.text, str) and payload.text.strip():
        return payload.text
    if payload.parameters:
        parameter_intent = payload.parameters.get("intent")
        if isinstance(parameter_intent, dict | str):
            return parameter_intent
        if _looks_like_intent(payload.parameters):
            return dict(payload.parameters)
    raise AdapterError(
        "TaskRequestV1 payload must include an intent or structured wallet task input.",
        code="invalid_payload",
        details={"task_id": payload.task_id},
    )


def _looks_like_intent(value: Mapping[str, Any]) -> bool:
    return isinstance(value.get("kind") or value.get("type") or value.get("intent"), str)


def _canonical_payload_kind(kind: str | None) -> str | None:
    if kind is None:
        return None
    return _PAYLOAD_KIND_ALIASES.get(kind, kind)


def _is_value_moving_intent(intent: dict[str, Any] | str) -> bool:
    if not isinstance(intent, Mapping):
        lowered = intent.lower()
        return any(kind.replace("_", " ") in lowered for kind in _VALUE_MOVING_KINDS)
    kind = intent.get("kind", intent.get("type", intent.get("intent")))
    return isinstance(kind, str) and kind.strip().lower() in _VALUE_MOVING_KINDS


def _effective_request_id(envelope: PanAgentEnvelope, request_id: str | None) -> str:
    return envelope.trace_id or request_id or envelope.id


def _effective_idempotency_key(
    envelope: PanAgentEnvelope,
    payload: UserMessageV1 | TaskRequestV1,
    header_idempotency_key: str | None,
) -> str | None:
    return (
        payload.idempotency_key
        or _string_metadata(envelope, "idempotency_key")
        or _string_metadata(envelope, "idempotencyKey")
        or header_idempotency_key
    )


def _required_identity(
    field: str,
    envelope: PanAgentEnvelope,
    payload_value: str | None,
) -> str:
    value = payload_value or _string_metadata(envelope, field)
    if value is None:
        raise AdapterError(f"Envelope payload must include {field}.", code="invalid_payload")
    return value


def _string_metadata(envelope: PanAgentEnvelope, key: str) -> str | None:
    value = envelope.metadata.get(key)
    return value if isinstance(value, str) and value else None


def _inbound_metadata(
    envelope: PanAgentEnvelope,
    payload_metadata: dict[str, Any],
) -> dict[str, Any]:
    metadata = dict(envelope.metadata)
    metadata.update(payload_metadata)
    metadata.update(
        {
            "schema_version": envelope.schema_version,
            "envelope_id": envelope.id,
            "trace_id": envelope.trace_id,
            "turn_id": envelope.turn_id,
            "step_id": envelope.step_id,
            "parent_step_id": envelope.parent_step_id,
            "from_role": envelope.from_role,
            "to_role": envelope.to_role,
            "payload_kind": envelope.payload_kind,
        }
    )
    if envelope.artifacts:
        metadata["artifacts"] = envelope.artifacts
    return {key: value for key, value in metadata.items() if value is not None}


def _response_metadata(
    mercury_request: MercuryInvokeRequest,
    idempotency_key: str | None,
) -> dict[str, Any]:
    metadata = dict(mercury_request.metadata)
    if idempotency_key is not None:
        metadata["idempotency_key"] = idempotency_key
    return cast(dict[str, Any], redact_value(metadata))


def _result_payload(response: MercuryInvokeResponse) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        redact_value(
            {
                "request_id": response.request_id,
                "status": response.status,
                "chain": response.chain,
                "data": response.data,
                "tx_hash": response.tx_hash,
                "receipt": response.receipt,
                "approval_required": response.approval_required,
            }
        ),
    )


def _task_id(mercury_request: MercuryInvokeRequest) -> str | None:
    value = mercury_request.metadata.get("task_id")
    return value if isinstance(value, str) else None


def _reply_envelope(
    inbound: PanAgentEnvelope,
    *,
    payload: dict[str, Any],
    error: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> PanAgentEnvelope:
    return PanAgentEnvelope(
        schema_version=inbound.schema_version,
        id=str(uuid4()),
        trace_id=inbound.trace_id,
        turn_id=inbound.turn_id,
        step_id=str(uuid4()),
        parent_step_id=inbound.step_id or inbound.parent_step_id,
        from_role=_MERCURY_ROLE,
        to_role=inbound.from_role,
        payload=redact_value(payload),
        artifacts=inbound.artifacts,
        metadata=metadata or {},
        error=error,
    )
