"""LiFi swap provider adapter.

1Claw: optional API key is read from ``get_settings().lifi_api_secret_path`` (default
``mercury/apis/lifi``) via :class:`mercury.swaps.base.SwapProviderConfig`. If no key is
configured, requests omit ``x-lifi-api-key`` and use LiFi's public tier.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mercury.models.addresses import normalize_evm_address
from mercury.models.execution import PreparedTransaction
from mercury.models.swaps import (
    SwapEVMTransaction,
    SwapExecution,
    SwapExecutionType,
    SwapProviderName,
    SwapQuote,
    SwapQuoteRequest,
    SwapRoute,
    SwapRouteKind,
)
from mercury.swaps.base import (
    JsonHttpClient,
    SwapProviderConfig,
    SwapProviderError,
    UrllibJsonHttpClient,
    provider_api_key,
    require_int,
    require_string,
)


class LiFiProvider:
    """LiFi quote/build adapter behind Mercury's normalized interface."""

    name = SwapProviderName.LIFI

    def __init__(
        self,
        config: SwapProviderConfig | None = None,
        *,
        http_client: JsonHttpClient | None = None,
    ) -> None:
        self._config = config or SwapProviderConfig.default_for(self.name)
        self._http = http_client or UrllibJsonHttpClient(self._config.base_url)

    def get_quote(self, request: SwapQuoteRequest) -> SwapQuote:
        """Fetch and normalize a LiFi quote response."""

        to_chain = _request_to_chain_id(request)
        response = self._http.get_json(
            "/quote",
            params={
                "fromChain": request.chain_id,
                "toChain": to_chain,
                "fromToken": normalize_evm_address(request.from_token),
                "toToken": request.to_token,
                "fromAmount": str(request.amount_in_raw),
                "fromAddress": request.wallet_address,
                "toAddress": request.effective_recipient,
                "slippage": _slippage_float(request.max_slippage_bps),
            },
            headers=_headers(self._config),
        )
        return self._normalize_quote(request, response)

    def build_execution(self, quote: SwapQuote) -> SwapExecution:
        """Build an EVM execution payload from LiFi's transaction request."""

        transaction_payload = quote.raw_quote.get("transactionRequest")
        if not isinstance(transaction_payload, dict):
            return SwapExecution(
                provider=self.name,
                execution_type=SwapExecutionType.UNSUPPORTED,
                quote=quote,
                unsupported_reason="LiFi quote did not include an executable transaction.",
            )

        transaction = SwapEVMTransaction(
            chain_id=quote.request.chain_id,
            to=require_string(transaction_payload, "to"),
            data=require_string(transaction_payload, "data"),
            value_wei=_parse_uint256(transaction_payload.get("value"), "transaction value"),
        )
        return SwapExecution(
            provider=self.name,
            execution_type=SwapExecutionType.EVM_TRANSACTION,
            quote=quote,
            transaction=transaction,
            raw_execution=transaction_payload,
        )

    def to_prepared_transaction(self, execution: SwapExecution) -> PreparedTransaction:
        """Convert a normalized LiFi EVM execution into the Phase 6 transaction model."""

        if execution.transaction is None:
            raise SwapProviderError("LiFi execution has no EVM transaction.")
        quote = execution.quote
        return PreparedTransaction(
            wallet_id=quote.request.wallet_id,
            chain=quote.request.chain,
            chain_id=execution.transaction.chain_id,
            from_address=quote.request.wallet_address,
            to=execution.transaction.to,
            value_wei=execution.transaction.value_wei,
            data=execution.transaction.data,
            idempotency_key=quote.request.idempotency_key,
            metadata={
                "action": "swap",
                "provider": self.name.value,
                "route_id": quote.route.route_id,
                "route_kind": quote.route.route_kind.value,
                "from_token": quote.route.from_token,
                "to_token": quote.route.to_token,
                "spender_address": quote.route.spender_address,
                "amount_in_raw": quote.amount_in_raw,
                "expected_amount_out_raw": quote.expected_amount_out_raw,
                "min_amount_out_raw": quote.min_amount_out_raw,
                "slippage_bps": quote.slippage_bps,
                "recipient_address": quote.recipient_address,
                "quote_expires_at": quote.expires_at.isoformat() if quote.expires_at else None,
            },
        )

    def _normalize_quote(self, request: SwapQuoteRequest, payload: dict[str, Any]) -> SwapQuote:
        action = payload.get("action")
        if not isinstance(action, dict):
            raise SwapProviderError("LiFi quote response missing action.")
        raw_estimate = payload.get("estimate")
        estimate: dict[str, Any] = raw_estimate if isinstance(raw_estimate, dict) else {}
        em = _flatten_estimate_view(payload, estimate)

        route_id = _route_id(payload)
        from_chain_id = require_int(action, "fromChainId")
        to_chain_id = require_int(action, "toChainId")
        spender = _spender(payload, estimate)
        try:
            expected_out = _require_amount_int(em, "toAmount")
        except SwapProviderError as exc:
            msg = "LiFi quote response missing toAmount in estimate or body."
            raise SwapProviderError(msg) from exc
        min_out = _optional_amount_key(em, "toAmountMin")
        try:
            amount_in = _require_amount_int(em, "fromAmount")
        except SwapProviderError as exc:
            msg = "LiFi quote response missing fromAmount in estimate or body."
            raise SwapProviderError(msg) from exc
        expires_at = _expiry(payload, estimate, em, action)
        route_kind = SwapRouteKind.BRIDGE if from_chain_id != to_chain_id else SwapRouteKind.SWAP

        from_token_route = _action_token_address(action, "fromToken", request.from_token)
        if request.from_token_is_native:
            from_token_route = normalize_evm_address(request.from_token)
        route = SwapRoute(
            provider=self.name,
            route_id=route_id,
            route_kind=route_kind,
            from_chain_id=from_chain_id,
            to_chain_id=to_chain_id,
            from_token=from_token_route,
            to_token=_action_token_address(action, "toToken", request.to_token),
            spender_address=spender,
            steps=_steps(payload),
        )
        return SwapQuote(
            provider=self.name,
            request=request,
            route=route,
            amount_in_raw=amount_in,
            expected_amount_out_raw=expected_out,
            min_amount_out_raw=min_out,
            slippage_bps=request.max_slippage_bps,
            expires_at=expires_at,
            recipient_address=request.effective_recipient,
            raw_quote=payload,
        )


def _action_token_address(action: dict[str, Any], key: str, fallback: str) -> str:
    """Resolve token address from LiFi ``action`` (string or ``Token`` object)."""

    raw = action.get(key)
    if isinstance(raw, str) and raw.strip():
        return normalize_evm_address(raw)
    if isinstance(raw, dict):
        addr = raw.get("address")
        if isinstance(addr, str) and addr.strip():
            return normalize_evm_address(addr)
    if fallback.strip():
        return normalize_evm_address(fallback)
    raise SwapProviderError(f"LiFi quote action missing token field '{key}'.")


def _request_to_chain_id(request: SwapQuoteRequest) -> int:
    if request.to_chain_id is not None:
        return request.to_chain_id
    return request.chain_id


def _headers(config: SwapProviderConfig) -> dict[str, str]:
    api_key = provider_api_key(config)
    return {"x-lifi-api-key": api_key} if api_key else {}


def _slippage_float(slippage_bps: int | None) -> float | None:
    if slippage_bps is None:
        return None
    return slippage_bps / 10_000


def _route_id(payload: dict[str, Any]) -> str:
    for key in ("id", "tool"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return "lifi-route"


def _spender(payload: dict[str, Any], estimate: dict[str, Any]) -> str | None:
    for source in (payload, estimate):
        for key in ("approvalAddress", "spenderAddress", "spender"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def _flatten_estimate_view(
    body: dict[str, Any], estimate: dict[str, Any]
) -> dict[str, Any]:
    """Merge top-level and estimate fields; estimate wins (LiFi /quote)."""

    keys = (
        "fromAmount",
        "toAmount",
        "toAmountMin",
        "approvalAddress",
        "spenderAddress",
        "expiresAt",
    )
    out: dict[str, Any] = {}
    for key in keys:
        if key in estimate and estimate.get(key) is not None:
            out[key] = estimate[key]
        elif key in body and body.get(key) is not None:
            out[key] = body[key]
    return out


def _optional_amount_key(payload: dict[str, Any], key: str) -> int | None:
    if key not in payload or payload.get(key) is None:
        return None
    return _coerce_non_negative_int(payload[key], key)


def _require_amount_int(payload: dict[str, Any], key: str) -> int:
    if key not in payload or payload.get(key) is None:
        raise SwapProviderError(f"Swap provider response missing amount field '{key}'.")
    return _coerce_non_negative_int(payload[key], key)


def _coerce_non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise SwapProviderError(f"Swap provider response field '{field}' must be a number.")
    if isinstance(value, int):
        if value < 0:
            raise SwapProviderError(f"Swap provider response field '{field}' must be non-negative.")
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise SwapProviderError(f"Swap provider response has invalid amount field '{field}'.")
        try:
            n = int(s, 10)
        except ValueError as exc:
            err = f"Swap provider response has invalid amount field '{field}'."
            raise SwapProviderError(err) from exc
        if n < 0:
            raise SwapProviderError(f"Swap provider response field '{field}' must be non-negative.")
        return n
    raise SwapProviderError(f"Swap provider response has invalid amount field '{field}'.")


def _expiry(
    payload: dict[str, Any],
    estimate: dict[str, Any],
    em: dict[str, Any],
    action: dict[str, Any],
) -> datetime:
    for source in (em, payload, estimate, action):
        value = _first_expires_at_key(source)
        if value is not None:
            return value
    return datetime.now(tz=UTC) + timedelta(minutes=5)


def _first_expires_at_key(source: Any) -> datetime | None:
    if not isinstance(source, dict):
        return None
    for k in ("expiresAt", "validUntil"):
        if k not in source:
            continue
        v = source[k]
        parsed = _parse_expiry_value(v)
        if parsed is not None:
            return parsed
    return None


def _parse_expiry_value(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = int(value)
        if ts > 1_000_000_000_000:
            return datetime.fromtimestamp(ts / 1000, tz=UTC)
        return datetime.fromtimestamp(ts, tz=UTC)
    if isinstance(value, str) and value.strip():
        s = value.strip()
        if "T" in s or s.count("-") >= 2:
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)
            except ValueError:
                return None
        if s.isdigit():
            ts = int(s)
            if ts > 1_000_000_000_000:
                return datetime.fromtimestamp(ts / 1000, tz=UTC)
            if ts > 1_000_000_000:
                return datetime.fromtimestamp(ts, tz=UTC)
    return None


def _parse_uint256(value: Any, field: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        raise SwapProviderError(f"Invalid {field}.")
    if isinstance(value, int):
        if value < 0:
            raise SwapProviderError(f"Invalid {field}.")
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("0x") or s.startswith("0X"):
            return int(s, 16)
        if s.isdecimal():
            return int(s)
    raise SwapProviderError(f"Invalid {field}.")


def _steps(payload: dict[str, Any]) -> tuple[str, ...]:
    included_steps = payload.get("includedSteps")
    if not isinstance(included_steps, list):
        return ()
    names: list[str] = []
    for step in included_steps:
        if isinstance(step, dict):
            tool = step.get("tool")
            if isinstance(tool, str) and tool.strip():
                names.append(tool)
    return tuple(names)
