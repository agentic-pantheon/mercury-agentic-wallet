"""CoW Swap adapter behind Mercury's normalized interface.

**API shape (v1, api.cow.fi)**

* Base host is per-environment (default ``https://api.cow.fi``). **Paths use a network
  slug**, not a numeric ``chain_id`` in the first segment, e.g.
  ``https://api.cow.fi/base/api/v1/quote`` and ``/base/api/v1/orders`` — not
  ``/8453/...``.
* The quote response includes a normalizing ``quote`` object, optional ``id``, optional
  ``typedData`` for EIP-712, and a spender/relayer (e.g. ``spender``, ``allowanceTarget``,
  or ``vaultRelayer``) used for ERC-20 approval policy.
* Order submission: ``POST {slug}/api/v1/orders`` with a JSON body that always includes
  CoW's ``order`` object plus a ``signature`` and ``signingScheme`` (e.g. ``eip712``) as
  per the CoW Order book API (https://docs.cow.fi/cow-protocol/reference/apis/orderbook).
  A successful response may be a JSON object or, in some deployments, a plain-text
  order UID; the client normalizes the latter to ``{"orderUid": "<id>"}``.
* Optional ``Authorization: Bearer <api-key>`` when configured via 1Claw; many
  public endpoints work without a key.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mercury.models.swaps import (
    SwapExecution,
    SwapExecutionType,
    SwapProviderName,
    SwapQuote,
    SwapQuoteRequest,
    SwapRoute,
    SwapRouteKind,
    SwapTypedOrder,
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

# Network suffix for https://api.cow.fi/{slug}/api/v1/... (see CoW host routing docs)
COW_NETWORK_SLUG_BY_CHAIN_ID: dict[int, str] = {
    1: "mainnet",
    100: "gnosis",
    42161: "arbitrum_one",
    8453: "base",
}

PostOrderFn = Callable[[str, dict[str, Any]], dict[str, Any]]


def cow_network_slug_for_chain_id(chain_id: int) -> str:
    """Return the CoW API first-path segment (network slug) for an EVM chain id."""

    try:
        return COW_NETWORK_SLUG_BY_CHAIN_ID[chain_id]
    except KeyError as exc:
        known = ", ".join(str(x) for x in sorted(COW_NETWORK_SLUG_BY_CHAIN_ID))
        raise SwapProviderError(
            f"CoW Swap has no network slug mapping for chain_id={chain_id}. Known: {known}."
        ) from exc


def _post_cow_order_urllib(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
) -> dict[str, Any]:
    """POST JSON to CoW; handle JSON or plain-text order-UID responses."""

    full_url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    request = Request(
        full_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except (HTTPError, URLError, OSError) as exc:
        raise SwapProviderError("CoW order submission request failed.") from exc
    body = body.strip()
    if not body:
        return {}
    try:
        parsed: Any = json.loads(body)
    except json.JSONDecodeError:
        return {"orderUid": body}
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str):
        return {"orderUid": parsed}
    raise SwapProviderError("CoW order submission returned an unexpected JSON value.")


class CowSwapProvider:
    """CoW Swap quote/order adapter.

    MVP support normalizes quotes and typed order payloads. Order submission remains
    outside the EVM transaction pipeline and must use the typed-data signer boundary.
    """

    name = SwapProviderName.COWSWAP

    def __init__(
        self,
        config: SwapProviderConfig | None = None,
        *,
        http_client: JsonHttpClient | None = None,
        post_order: PostOrderFn | None = None,
    ) -> None:
        self._config = config or SwapProviderConfig.default_for(self.name)
        self._http = http_client or UrllibJsonHttpClient(self._config.base_url)
        if post_order is not None:
            self._post_order: PostOrderFn = post_order
        else:
            self._post_order = _default_post_order(self._config)

    def get_quote(self, request: SwapQuoteRequest) -> SwapQuote:
        """Fetch and normalize a CoW Swap quote."""

        if request.from_token_is_native:
            raise SwapProviderError(
                "CoW Swap quotes do not support selling the chain native token in this "
                "adapter; use wrapped native or another provider."
            )

        slug = cow_network_slug_for_chain_id(request.chain_id)
        response = self._http.post_json(
            f"{slug}/api/v1/quote",
            payload={
                "sellToken": request.from_token,
                "buyToken": request.to_token,
                "receiver": request.effective_recipient,
                "from": request.wallet_address,
                "sellAmountBeforeFee": str(request.amount_in_raw),
                "kind": "sell",
            },
            headers=_headers(self._config),
        )
        quote_payload = response.get("quote")
        if not isinstance(quote_payload, dict):
            raise SwapProviderError("CoW Swap response missing quote payload.")

        route_id = (
            require_string(response, "id")
            if isinstance(response.get("id"), str)
            else "cowswap-quote"
        )
        route = SwapRoute(
            provider=self.name,
            route_id=route_id,
            route_kind=SwapRouteKind.SWAP,
            from_chain_id=request.chain_id,
            to_chain_id=request.chain_id,
            from_token=require_string(quote_payload, "sellToken"),
            to_token=require_string(quote_payload, "buyToken"),
            spender_address=_spender(response, quote_payload),
            steps=("cow-protocol-order",),
        )
        return SwapQuote(
            provider=self.name,
            request=request,
            route=route,
            amount_in_raw=require_int(quote_payload, "sellAmount"),
            expected_amount_out_raw=require_int(quote_payload, "buyAmount"),
            min_amount_out_raw=None,
            slippage_bps=request.max_slippage_bps,
            expires_at=_expiry(quote_payload),
            recipient_address=request.effective_recipient,
            raw_quote=response,
        )

    def build_execution(self, quote: SwapQuote) -> SwapExecution:
        """Build a typed order payload or return an explicit unsupported result."""

        order_payload = quote.raw_quote.get("order") or quote.raw_quote.get("quote")
        if not isinstance(order_payload, dict):
            return SwapExecution(
                provider=self.name,
                execution_type=SwapExecutionType.UNSUPPORTED,
                quote=quote,
                unsupported_reason="CoW Swap quote did not include an order payload.",
            )
        typed_data = quote.raw_quote.get("typedData")
        if not isinstance(typed_data, dict):
            return SwapExecution(
                provider=self.name,
                execution_type=SwapExecutionType.UNSUPPORTED,
                quote=quote,
                unsupported_reason="CoW Swap typed-data signing is not available for this quote.",
                raw_execution=order_payload,
            )

        slug = cow_network_slug_for_chain_id(quote.request.chain_id)
        base = self._config.base_url.rstrip("/")
        return SwapExecution(
            provider=self.name,
            execution_type=SwapExecutionType.EIP712_ORDER,
            quote=quote,
            order=SwapTypedOrder(
                chain_id=quote.request.chain_id,
                typed_data=typed_data,
                submit_url=f"{base}/{slug}/api/v1/orders",
            ),
            raw_execution=order_payload,
        )

    def submit_order(
        self,
        *,
        chain_id: int,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """POST a signed CoW order JSON (``order``, ``signature``, ``signingScheme``, ...).

        The signing/custody layer must build a valid body for the CoW Order book API.
        """

        slug = cow_network_slug_for_chain_id(chain_id)
        return self._post_order(f"{slug}/api/v1/orders", body)


def _default_post_order(config: SwapProviderConfig) -> PostOrderFn:
    base_url = config.base_url

    def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        return _post_cow_order_urllib(base_url, path, body, headers=_headers(config))

    return _post


def _headers(config: SwapProviderConfig) -> dict[str, str]:
    api_key = provider_api_key(config)
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _spender(response: dict[str, Any], quote_payload: dict[str, Any]) -> str | None:
    for container in (response, quote_payload):
        for key in ("spender", "allowanceTarget", "vaultRelayer"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def _expiry(quote_payload: dict[str, Any]) -> datetime:
    value = quote_payload.get("validTo")
    if isinstance(value, int):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        if value.isdecimal():
            return datetime.fromtimestamp(int(value), tz=UTC)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return datetime.now(tz=UTC) + timedelta(minutes=5)
