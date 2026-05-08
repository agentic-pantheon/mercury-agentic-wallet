"""Uniswap Trading API adapter behind Mercury's normalized interface.

The default HTTP base URL matches :class:`mercury.swaps.base.SwapProviderConfig`
(``https://api.uniswap.org``), with routes such as ``POST /v1/quote`` and
``POST /v1/swap``.

Authentication uses a Bearer token when configured: the API key is read from
``get_settings().uniswap_api_secret_path`` (default ``mercury/apis/uniswap``)
via 1Claw / :class:`mercury.swaps.base.SwapProviderConfig` and sent as
``Authorization: Bearer <key>``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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
    require_string,
)


class UniswapProvider:
    """Uniswap quote/build adapter with API details isolated behind this class."""

    name = SwapProviderName.UNISWAP

    def __init__(
        self,
        config: SwapProviderConfig | None = None,
        *,
        http_client: JsonHttpClient | None = None,
    ) -> None:
        self._config = config or SwapProviderConfig.default_for(self.name)
        self._http = http_client or UrllibJsonHttpClient(self._config.base_url)

    def get_quote(self, request: SwapQuoteRequest) -> SwapQuote:
        """Fetch and normalize a Uniswap quote."""

        if request.from_token_is_native:
            raise SwapProviderError(
                "Uniswap Trading API quotes do not support selling the chain native token "
                "in this adapter; use wrapped native or another provider."
            )

        response = self._http.post_json(
            "/v1/quote",
            payload={
                "tokenInChainId": request.chain_id,
                "tokenOutChainId": request.chain_id,
                "tokenIn": request.from_token,
                "tokenOut": request.to_token,
                "amount": str(request.amount_in_raw),
                "swapper": request.wallet_address,
                "recipient": request.effective_recipient,
                "type": "EXACT_INPUT",
            },
            headers=_headers(self._config),
        )
        quote_payload = _merged_quote_view(response)
        route = SwapRoute(
            provider=self.name,
            route_id=_route_id(quote_payload),
            route_kind=SwapRouteKind.SWAP,
            from_chain_id=_require_chain_id(quote_payload, "tokenInChainId"),
            to_chain_id=_require_chain_id(quote_payload, "tokenOutChainId"),
            from_token=require_string(quote_payload, "tokenIn"),
            to_token=require_string(quote_payload, "tokenOut"),
            spender_address=_spender(quote_payload),
            steps=("uniswap",),
        )
        amount_in = _require_amount_int(
            quote_payload,
            "amount",
            "amountIn",
            "inputAmount",
        )
        if amount_in != request.amount_in_raw:
            raise SwapProviderError("Uniswap quote amount_in does not match the request.")
        expected_out = _require_amount_int(
            quote_payload,
            "amountOut",
            "quoteAmountOut",
            "outputAmount",
            "outAmount",
        )
        min_out = _optional_amount_int(
            quote_payload,
            "minAmountOut",
            "amountOutMinimum",
            "quoteAmountOutMinimum",
        )
        return SwapQuote(
            provider=self.name,
            request=request,
            route=route,
            amount_in_raw=amount_in,
            expected_amount_out_raw=expected_out,
            min_amount_out_raw=min_out,
            slippage_bps=_resolve_slippage_bps(quote_payload, request),
            expires_at=_expiry(quote_payload),
            recipient_address=request.effective_recipient,
            raw_quote=response,
        )

    def build_execution(self, quote: SwapQuote) -> SwapExecution:
        """Build a Uniswap EVM execution payload or return explicit unsupported."""

        response = self._http.post_json(
            "/v1/swap",
            payload={"quote": _swap_quote_argument(quote)},
            headers=_headers(self._config),
        )
        transaction_payload = response.get("transaction")
        if not isinstance(transaction_payload, dict):
            return SwapExecution(
                provider=self.name,
                execution_type=SwapExecutionType.UNSUPPORTED,
                quote=quote,
                unsupported_reason="Uniswap response did not include an executable transaction.",
                raw_execution=response,
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
            raw_execution=response,
        )

    def to_prepared_transaction(self, execution: SwapExecution) -> PreparedTransaction:
        """Convert a normalized Uniswap execution into the Phase 6 transaction model."""

        if execution.transaction is None:
            raise SwapProviderError("Uniswap execution has no EVM transaction.")
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


def _headers(config: SwapProviderConfig) -> dict[str, str]:
    api_key = provider_api_key(config)
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _merged_quote_view(response: dict[str, Any]) -> dict[str, Any]:
    """Prefer inner ``quote`` when present; merge outer siblings so split shapes work."""

    inner = response.get("quote")
    if isinstance(inner, dict):
        outer = {k: v for k, v in response.items() if k != "quote"}
        return {**outer, **inner}
    return response


def _route_id(payload: dict[str, Any]) -> str:
    for key in ("requestId", "quoteId", "routeId", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return "uniswap-route"


def _spender(payload: dict[str, Any]) -> str | None:
    for key in (
        "permit2Address",
        "allowanceTarget",
        "allowanceHolder",
        "spender",
        "approvalAddress",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _require_chain_id(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if value is None and key == "tokenInChainId":
        value = payload.get("chainId")
    if value is None and key == "tokenOutChainId":
        value = payload.get("chainId")
    if isinstance(value, bool):
        raise SwapProviderError(f"Swap provider response field '{key}' must be an integer.")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        return int(value.strip())
    raise SwapProviderError(f"Swap provider response missing integer field '{key}'.")


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


def _require_amount_int(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in payload and payload[key] is not None:
            return _coerce_non_negative_int(payload[key], key)
    raise SwapProviderError(f"Swap provider response missing amount field; tried {keys!r}.")


def _optional_amount_int(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in payload and payload[key] is not None:
            return _coerce_non_negative_int(payload[key], key)
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


def _expiry(payload: dict[str, Any]) -> datetime | None:
    for key in ("expiresAt", "deadline", "validUntil", "quoteExpiry", "expiration"):
        parsed = _parse_expiry_value(payload.get(key))
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


def _resolve_slippage_bps(payload: dict[str, Any], request: SwapQuoteRequest) -> int | None:
    raw = payload.get("slippageBps")
    if raw is not None and not isinstance(raw, bool):
        if isinstance(raw, int):
            bps = raw
        elif isinstance(raw, str) and raw.strip().isdecimal():
            bps = int(raw.strip())
        else:
            bps = None
        if bps is not None:
            if bps < 0 or bps > 10_000:
                raise SwapProviderError("Uniswap quote slippageBps is out of range.")
            return bps
    tol = payload.get("slippageTolerance")
    if isinstance(tol, (int, float)) and not isinstance(tol, bool):
        return int(round(float(tol) * 10_000))
    if isinstance(tol, str) and tol.strip():
        s = tol.strip()
        if s.isdecimal():
            v = int(s)
            if 0 <= v <= 10_000:
                return v
        try:
            return int(round(float(s) * 10_000))
        except ValueError:
            pass
    return request.max_slippage_bps


def _swap_quote_argument(quote: SwapQuote) -> dict[str, Any]:
    """Object expected under the ``quote`` key for ``POST /v1/swap``.

    When the quote response wrapped routing data in a nested ``quote`` object,
    that inner dict is what the swap endpoint typically expects. Otherwise the
    full quote response body is passed through.
    """

    raw = quote.raw_quote
    inner = raw.get("quote")
    if isinstance(inner, dict):
        return inner
    if isinstance(raw, dict):
        return raw
    raise SwapProviderError("Uniswap raw_quote must be a dict for swap.")
