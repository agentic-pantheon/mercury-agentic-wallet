"""FastAPI application factory and Mercury-native routes."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response

from mercury.chains import list_chains
from mercury.config import MercurySettings
from mercury.custody.errors import SecretNotFoundError
from mercury.graph.runtime import GraphRuntime
from mercury.graph.webhook_graph import compiled_alchemy_webhook_graph
from mercury.invoke import MercuryInvoker, get_invoke_guide_markdown
from mercury.service.dependencies import get_graph_runtime, get_secret_store, get_service_settings
from mercury.service.errors import DependencyUnavailableError, install_exception_handlers
from mercury.service.http_logging import MercuryHttpLoggingMiddleware
from mercury.service.logging import (
    configure_service_logging,
    log_service_event,
    parse_mercury_log_level,
)
from mercury.service.models import (
    HealthResponse,
    MercuryInvokeRequest,
    MercuryInvokeResponse,
    ReadinessResponse,
)
from mercury.webhooks.alchemy_dedupe import AlchemyWebhookDedupeStore
from mercury.webhooks.alchemy_handler import merge_watched_addresses
from mercury.webhooks.alchemy_verify import is_valid_signature_for_string_body
from mercury.webhooks.keys import resolve_alchemy_webhook_signing_key


def _alchemy_webhook_dedupe_store(app: FastAPI) -> AlchemyWebhookDedupeStore:
    """Return a process-local dedupe store for Alchemy webhook retries."""

    existing = getattr(app.state, "alchemy_webhook_dedupe", None)
    if isinstance(existing, AlchemyWebhookDedupeStore):
        return existing
    store = AlchemyWebhookDedupeStore()
    app.state.alchemy_webhook_dedupe = store
    return store


def _alchemy_webhook_compiled_graph(app: FastAPI) -> Any:
    """Lazily compile the webhook graph once per app, sharing a dedupe store."""

    cached = getattr(app.state, "alchemy_webhook_graph", None)
    if cached is not None:
        return cached
    compiled = compiled_alchemy_webhook_graph(_alchemy_webhook_dedupe_store(app))
    app.state.alchemy_webhook_graph = compiled
    return compiled


def create_app(
    *,
    settings: MercurySettings | None = None,
    runtime: GraphRuntime | None = None,
) -> FastAPI:
    """Create the Mercury FastAPI app without touching external services."""

    effective_settings = settings or MercurySettings()
    configure_service_logging(level=parse_mercury_log_level(effective_settings.log_level))
    app = FastAPI(title=effective_settings.app_name)
    app.state.settings = effective_settings
    if runtime is not None:
        app.state.graph_runtime = runtime

    install_exception_handlers(app)
    app.add_middleware(MercuryHttpLoggingMiddleware)

    @app.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok", service=effective_settings.app_name)

    @app.get("/readyz", response_model=ReadinessResponse)
    def readyz() -> ReadinessResponse:
        chains = list_chains()
        supported = [chain.name for chain in chains]
        return ReadinessResponse(
            status="ready",
            service=effective_settings.app_name,
            default_chain=effective_settings.default_chain,
            supported_chains=supported,
        )

    @app.get("/v1/mercury/invoke/guide")
    def mercury_invoke_guide() -> Response:
        """Return Markdown instructions for using ``POST /v1/mercury/invoke``."""

        return Response(
            content=get_invoke_guide_markdown(),
            media_type="text/markdown; charset=utf-8",
        )

    @app.post("/v1/mercury/invoke", response_model=MercuryInvokeResponse)
    def invoke_mercury(
        request: Request,
        payload: MercuryInvokeRequest,
        graph_runtime: Annotated[GraphRuntime, Depends(get_graph_runtime)],
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> MercuryInvokeResponse:
        request_id = payload.effective_request_id(x_request_id)
        request.state.request_id = request_id
        return MercuryInvoker(graph_runtime).invoke(
            payload,
            x_request_id=x_request_id,
            idempotency_key=idempotency_key,
        )

    @app.post("/v1/webhooks/alchemy/address-activity")
    async def alchemy_address_activity_webhook(
        request: Request,
        settings: Annotated[MercurySettings, Depends(get_service_settings)],
        watched_addresses: Annotated[
            str | None,
            Query(
                description=(
                    "Optional comma-separated 0x addresses. When set, only rows whose normalized "
                    "toAddress is in this set are emitted. When omitted, every activity "
                    "row is eligible."
                ),
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Receive Alchemy Notify Address Activity webhooks (HMAC-verified raw body)."""

        raw_body = await request.body()
        signature = request.headers.get("X-Alchemy-Signature") or request.headers.get(
            "x-alchemy-signature"
        )
        if not signature:
            raise HTTPException(status_code=401, detail="Missing X-Alchemy-Signature header.")

        overlay = getattr(request.app.state, "alchemy_webhook_signing_key", None)
        secret_store = None
        if not (isinstance(overlay, str) and overlay.strip()):
            try:
                secret_store = get_secret_store(settings)
            except DependencyUnavailableError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Secret store is not configured; cannot verify webhook signature.",
                ) from exc

        try:
            signing_key = resolve_alchemy_webhook_signing_key(
                request=request,
                settings=settings,
                secret_store=secret_store,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail="Alchemy webhook signing key is not configured.",
            ) from exc
        except SecretNotFoundError as exc:
            raise HTTPException(
                status_code=503,
                detail="Alchemy webhook signing key secret is missing.",
            ) from exc

        if not is_valid_signature_for_string_body(raw_body, signature, signing_key):
            raise HTTPException(status_code=403, detail="Invalid webhook signature.")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Request body is not valid JSON.") from exc

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON payload must be an object.")

        watched = merge_watched_addresses(watched_addresses, payload)
        graph = _alchemy_webhook_compiled_graph(request.app)
        result = graph.invoke({"payload": payload, "watched_addresses": watched})
        summary = result.get("emit_summary")
        if not isinstance(summary, dict):
            return {"received": True, "skipped": False, "processed": 0, "alerts": []}
        return summary

    return app


app = create_app()
