"""HTTP request/response logging at the ASGI boundary (redacted)."""

from __future__ import annotations

import json
import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from mercury.service.logging import log_service_event, redact_value

_MAX_BODY_BYTES = 64 * 1024


def _parse_redacted_json_body(raw: bytes) -> Any:
    if not raw:
        return None
    if len(raw) > _MAX_BODY_BYTES:
        return {"_truncated": True, "bytes": len(raw)}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"_non_json": True, "bytes": len(raw)}
    return redact_value(parsed)


async def _read_request_body_for_log(request: Request) -> Any | None:
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        return {"_skipped": "non_json_content_type", "content_type": content_type or None}
    raw = await request.body()
    return _parse_redacted_json_body(raw)


def _should_capture_response_body(response: Response) -> bool:
    media_type = response.media_type or ""
    content_type = response.headers.get("content-type", "")
    if "json" not in media_type and "json" not in content_type:
        return False
    content_length = response.headers.get("content-length")
    if content_length is None:
        return False
    try:
        return 0 < int(content_length) <= _MAX_BODY_BYTES
    except ValueError:
        return False


class MercuryHttpLoggingMiddleware(BaseHTTPMiddleware):
    """Log each HTTP request and response with timing and redacted JSON bodies when safe."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        header_request_id = request.headers.get("x-request-id")
        query = str(request.query_params) if request.query_params else ""
        body_log = await _read_request_body_for_log(request)
        client = request.client
        log_service_event(
            "http_request",
            summary=f"Incoming HTTP {request.method} {request.url.path}",
            request_id=header_request_id,
            method=request.method,
            path=request.url.path,
            query=redact_value(query) if query else "",
            client_host=client.host if client else None,
            body=body_log,
        )

        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        state_request_id = getattr(request.state, "request_id", None)
        response_body: Any | None = None
        response_body_logged = False

        if _should_capture_response_body(response):
            chunks: list[bytes] = []
            async for block in response.body_iterator:  # type: ignore[attr-defined]
                chunks.append(block)
            raw = b"".join(chunks)
            response_body = _parse_redacted_json_body(raw)
            response_body_logged = True
            log_service_event(
                "http_response",
                summary=(
                    f"HTTP {request.method} {request.url.path} -> {response.status_code} "
                    f"in {duration_ms}ms (body logged)"
                ),
                request_id=state_request_id or header_request_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
                content_length=str(len(raw)),
                response_body_logged=response_body_logged,
                response_body=response_body,
            )
            return Response(
                content=raw,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
                background=getattr(response, "background", None),
            )

        log_service_event(
            "http_response",
            summary=(
                f"HTTP {request.method} {request.url.path} -> {response.status_code} "
                f"in {duration_ms}ms"
            ),
            request_id=state_request_id or header_request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            content_length=response.headers.get("content-length"),
            response_body_logged=response_body_logged,
            response_body=response_body,
        )
        return response
