"""Sanitized FastAPI exception mapping for Mercury service endpoints."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, cast

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from mercury.chains import UnsupportedChainError
from mercury.custody import CustodyError
from mercury.service.logging import log_service_event, redact_error_message, redact_value


class MercuryServiceError(RuntimeError):
    """Base class for errors that can be returned safely by the service."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    public_message = "Mercury request failed."
    error_code: ClassVar[str] = "internal_error"
    error_category: ClassVar[str] = "internal"

    def __init__(self, message: str | None = None, *, status_code: int | None = None) -> None:
        super().__init__(message or self.public_message)
        if status_code is not None:
            self.status_code = status_code


class DependencyUnavailableError(MercuryServiceError):
    """Raised when runtime dependencies cannot be constructed."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    public_message = "Mercury dependencies are unavailable."
    error_code = "dependency_unavailable"
    error_category = "internal"


class GraphInvocationError(MercuryServiceError):
    """Raised when graph execution fails outside domain-normalized graph state."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    public_message = "Mercury graph invocation failed."
    error_code = "graph_invocation_failed"
    error_category = "internal"


_LLM_INTERNAL = (
    "Retry once with backoff. If it still fails, stop and report an internal error "
    "without exposing raw errors or secrets."
)
_LLM_VALIDATION = "Inspect validation details, correct the payload, and retry."
_LLM_CUSTODY = "Explain the custody constraint and what the user can change."
_LLM_CHAIN = "Retry with a supported chain name from the service configuration."


def install_exception_handlers(app: FastAPI) -> None:
    """Register sanitized exception handlers on a FastAPI app."""

    app.add_exception_handler(RequestValidationError, cast(Any, _validation_exception_handler))
    app.add_exception_handler(MercuryServiceError, cast(Any, _service_exception_handler))
    app.add_exception_handler(CustodyError, cast(Any, _custody_exception_handler))
    app.add_exception_handler(UnsupportedChainError, cast(Any, _chain_exception_handler))
    app.add_exception_handler(Exception, cast(Any, _unhandled_exception_handler))


async def _validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    request_id = _request_id(request)
    details = jsonable_encoder(redact_value(exc.errors()))
    log_service_event(
        "request_validation_error",
        summary=(
            "Rejecting request: payload failed FastAPI/Pydantic validation "
            f"for {request.url.path}"
        ),
        level=logging.WARNING,
        request_id=request_id,
        path=request.url.path,
        errors=details,
    )
    return _error_response(
        request_id=request_id,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message="Request validation failed.",
        code="validation_failed",
        category="validation",
        retryable=False,
        recoverable=True,
        user_action="Fix the invalid fields and try again.",
        llm_action=_LLM_VALIDATION,
        details=details,
    )


async def _service_exception_handler(request: Request, exc: MercuryServiceError) -> JSONResponse:
    request_id = _request_id(request)
    log_service_event(
        "service_error",
        summary=f"Returning HTTP {exc.status_code} for {request.url.path}: {type(exc).__name__}",
        level=logging.WARNING if exc.status_code < 500 else logging.ERROR,
        request_id=request_id,
        path=request.url.path,
        error=exc,
    )
    msg = redact_error_message(exc)
    retryable = exc.status_code >= 500
    return _error_response(
        request_id=request_id,
        status_code=exc.status_code,
        message=msg,
        code=type(exc).error_code,
        category=type(exc).error_category,
        retryable=retryable,
        recoverable=True,
        user_action="Try again; contact support if the problem continues.",
        llm_action=_LLM_INTERNAL,
    )


async def _custody_exception_handler(request: Request, exc: CustodyError) -> JSONResponse:
    request_id = _request_id(request)
    log_service_event(
        "custody_error",
        summary=f"Custody/secret error on {request.url.path} — client should fix wallet or paths",
        level=logging.WARNING,
        request_id=request_id,
        path=request.url.path,
        error=exc,
    )
    return _error_response(
        request_id=request_id,
        status_code=status.HTTP_400_BAD_REQUEST,
        message=redact_error_message(exc),
        code="custody_error",
        category="policy",
        retryable=False,
        recoverable=True,
        user_action="Adjust the request or wallet configuration.",
        llm_action=_LLM_CUSTODY,
    )


async def _chain_exception_handler(request: Request, exc: UnsupportedChainError) -> JSONResponse:
    request_id = _request_id(request)
    log_service_event(
        "chain_error",
        summary=f"Unsupported or unknown chain in request to {request.url.path}",
        level=logging.WARNING,
        request_id=request_id,
        path=request.url.path,
        error=exc,
    )
    return _error_response(
        request_id=request_id,
        status_code=status.HTTP_400_BAD_REQUEST,
        message=redact_error_message(exc),
        code="unsupported_chain",
        category="config",
        retryable=False,
        recoverable=True,
        user_action="Choose a supported chain name.",
        llm_action=_LLM_CHAIN,
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = _request_id(request)
    log_service_event(
        "unhandled_error",
        summary=f"Unhandled exception on {request.url.path} — returning generic 500",
        level=logging.ERROR,
        request_id=request_id,
        path=request.url.path,
        error=exc,
    )
    return _error_response(
        request_id=request_id,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message="Internal server error.",
        code="internal_error",
        category="internal",
        retryable=True,
        recoverable=True,
        user_action="Try again; contact support if the problem continues.",
        llm_action=_LLM_INTERNAL,
    )


def _error_response(
    *,
    request_id: str | None,
    status_code: int,
    message: str,
    code: str,
    category: str,
    retryable: bool = False,
    recoverable: bool = True,
    user_action: str | None = None,
    llm_action: str | None = None,
    details: list[dict[str, Any]] | dict[str, Any] | None = None,
) -> JSONResponse:
    safe_message = redact_error_message(message)
    safe_user = redact_error_message(user_action) if user_action else None
    safe_llm = redact_error_message(llm_action) if llm_action else None
    if details is None:
        safe_details: list[Any] | dict[str, Any] = {}
    else:
        safe_details = redact_value(details)
        if safe_details is None:
            safe_details = {}
    if not isinstance(safe_details, dict | list):
        safe_details = {"value": safe_details}
    payload: dict[str, Any] = {
        "request_id": request_id,
        "status": "error",
        "message": safe_message,
        "error": {
            "code": code,
            "category": category,
            "message": safe_message,
            "retryable": retryable,
            "recoverable": recoverable,
            "user_action": safe_user,
            "llm_action": safe_llm,
            "details": safe_details,
        },
    }
    return JSONResponse(status_code=status_code, content=payload)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None
