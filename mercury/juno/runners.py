"""In-process runner for Mercury invoke (Juno specialist tool)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from mercury.graph.runtime import GraphRuntime
from mercury.invoke import get_invoke_guide_markdown, invoke_mercury
from mercury.juno.assistant_turn import (
    AssistantTurnAgentError,
    AssistantTurnResult,
    parse_mercury_body,
)
from mercury.service.errors import GraphInvocationError
from mercury.service.models import MercuryInvokeRequest, MercuryInvokeResponse

logger = logging.getLogger(__name__)

_LOCAL_INVOKE_GUIDE_PATH = "/mercury/invoke-guide"


@runtime_checkable
class MercuryAssistantRunnerLike(Protocol):
    """Minimal surface used by :func:`mercury.juno.subagent.build_mercury_juno_subagent`."""

    def fetch_get_text(self, relative_path: str) -> str: ...

    def run_turn(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> AssistantTurnResult: ...


def _local_response_to_result(response: MercuryInvokeResponse) -> AssistantTurnResult:
    raw = response.model_dump(mode="json")
    error = raw.get("error")
    if isinstance(error, dict):
        msg = error.get("message") or raw.get("message") or "Mercury request failed"
        code = error.get("code")
        details = error.get("details")
        return AssistantTurnAgentError(
            message=str(msg),
            code=str(code) if code is not None else None,
            details=details if isinstance(details, dict) else None,
        )
    status = raw.get("status")
    if status in {"failed", "rejected"}:
        msg = raw.get("message") or "Mercury request failed"
        return AssistantTurnAgentError(message=str(msg), code=str(status))
    return parse_mercury_body(raw)


class LocalMercuryAssistantRunner:
    """In-process Mercury runner using :func:`mercury.invoke.invoke_mercury`."""

    def __init__(
        self,
        runtime: GraphRuntime,
        *,
        send_x_request_id: bool = True,
    ) -> None:
        self._runtime = runtime
        self._send_x_request_id = send_x_request_id

    def fetch_get_text(self, relative_path: str) -> str:
        path = relative_path.strip()
        if not path.startswith("/"):
            path = "/" + path
        if path.rstrip("/") == _LOCAL_INVOKE_GUIDE_PATH.rstrip("/"):
            return get_invoke_guide_markdown()
        return f"(Local guide unavailable: unsupported path {path})"

    def run_turn(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> AssistantTurnResult:
        return self._execute_local_turn(payload, idempotency_key=idempotency_key)

    def _execute_local_turn(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None,
    ) -> AssistantTurnResult:
        body = dict(payload)
        if idempotency_key is not None:
            body.setdefault("idempotency_key", idempotency_key)
        try:
            request = MercuryInvokeRequest.model_validate(body)
        except ValidationError as exc:
            return AssistantTurnAgentError(
                message="Mercury request validation failed",
                code="validation_error",
                details={"errors": exc.errors()},
            )
        rid = str(uuid.uuid4()) if self._send_x_request_id else None
        try:
            response = invoke_mercury(
                self._runtime,
                request,
                x_request_id=rid,
                idempotency_key=idempotency_key,
            )
        except GraphInvocationError as exc:
            return AssistantTurnAgentError(
                message=str(exc) if str(exc) else "Mercury graph invocation failed",
                code=GraphInvocationError.error_code,
                details=None,
            )
        except Exception as exc:
            logger.exception("Mercury local invocation failed")
            return AssistantTurnAgentError(
                message="Mercury invocation failed unexpectedly",
                code="internal_error",
                details={"error_type": type(exc).__name__},
            )
        return _local_response_to_result(response)

    async def arun_turn(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> AssistantTurnResult:
        return await asyncio.to_thread(self.run_turn, payload, idempotency_key=idempotency_key)


__all__ = ["LocalMercuryAssistantRunner", "MercuryAssistantRunnerLike"]
