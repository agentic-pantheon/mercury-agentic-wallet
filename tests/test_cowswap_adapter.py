from datetime import UTC, datetime, timedelta

import pytest
from mercury.models.erc20 import ZERO_ADDRESS
from mercury.models.swaps import SwapExecutionType, SwapQuoteRequest
from mercury.swaps.base import SwapProviderError
from mercury.swaps.cowswap import CowSwapProvider, cow_network_slug_for_chain_id

TOKEN_IN = "0x000000000000000000000000000000000000cafE"
TOKEN_OUT = "0x000000000000000000000000000000000000dEaD"
WALLET = "0x000000000000000000000000000000000000bEEF"
SPENDER = "0x0000000000000000000000000000000000000002"


def test_cow_network_slug_maps_base() -> None:
    assert cow_network_slug_for_chain_id(8453) == "base"


def test_cowswap_quote_uses_network_slug_path_not_chain_id() -> None:
    client = FakeHttpClient(_response(include_typed_data=True))
    provider = CowSwapProvider(http_client=client)

    provider.get_quote(_request())

    assert client.paths == ["base/api/v1/quote"]


def test_cowswap_rejects_native_sell_with_clear_error() -> None:
    client = FakeHttpClient(_response(include_typed_data=True))
    provider = CowSwapProvider(http_client=client)

    with pytest.raises(SwapProviderError, match="native token"):
        provider.get_quote(_native_request())

    assert client.paths == []


def test_cowswap_quote_normalizes_order_route() -> None:
    provider = CowSwapProvider(http_client=FakeHttpClient(_response(include_typed_data=True)))

    quote = provider.get_quote(_request())

    assert quote.route.spender_address == SPENDER
    assert quote.amount_in_raw == 1_500_000
    assert quote.expected_amount_out_raw == 1_000_000


def test_cowswap_build_execution_submit_url_uses_slug() -> None:
    provider = CowSwapProvider(http_client=FakeHttpClient(_response(include_typed_data=True)))
    quote = provider.get_quote(_request())

    execution = provider.build_execution(quote)

    assert execution.order is not None
    assert execution.order.submit_url == "https://api.cow.fi/base/api/v1/orders"


def test_cowswap_build_execution_returns_typed_order_when_available() -> None:
    provider = CowSwapProvider(http_client=FakeHttpClient(_response(include_typed_data=True)))
    quote = provider.get_quote(_request())

    execution = provider.build_execution(quote)

    assert execution.execution_type == SwapExecutionType.EIP712_ORDER
    assert execution.order is not None
    assert execution.order.typed_data["domain"]["chainId"] == 8453


def test_cowswap_build_execution_explicitly_defers_without_typed_data() -> None:
    provider = CowSwapProvider(http_client=FakeHttpClient(_response(include_typed_data=False)))
    quote = provider.get_quote(_request())

    execution = provider.build_execution(quote)

    assert execution.execution_type == SwapExecutionType.UNSUPPORTED
    assert "typed-data" in (execution.unsupported_reason or "")


def test_cowswap_submit_order_posts_to_orders_path() -> None:
    recorded: list[tuple[str, dict[str, object]]] = []

    def post_order(path: str, body: dict[str, object]) -> dict[str, object]:
        recorded.append((path, body))
        return {"orderUid": "0xc0ffee"}

    provider = CowSwapProvider(
        http_client=FakeHttpClient(_response(include_typed_data=True)),
        post_order=post_order,
    )

    out = provider.submit_order(
        chain_id=8453,
        body={"order": {"foo": "bar"}, "signature": "0xsig", "signingScheme": "eip712"},
    )

    assert out == {"orderUid": "0xc0ffee"}
    assert recorded[0][0] == "base/api/v1/orders"
    assert recorded[0][1]["signingScheme"] == "eip712"


class FakeHttpClient:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.paths: list[str] = []

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        raise AssertionError("CoW Swap quote test should not GET")

    def post_json(
        self,
        path: str,
        *,
        payload: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.paths.append(path)
        return self._response


def _request() -> SwapQuoteRequest:
    return SwapQuoteRequest(
        wallet_id="primary",
        wallet_address=WALLET,
        chain="base",
        chain_id=8453,
        from_token=TOKEN_IN,
        to_token=TOKEN_OUT,
        amount_in="1.5",
        amount_in_raw=1_500_000,
        idempotency_key="swap-1",
    )


def _native_request() -> SwapQuoteRequest:
    return SwapQuoteRequest(
        wallet_id="primary",
        wallet_address=WALLET,
        chain="base",
        chain_id=8453,
        from_token=ZERO_ADDRESS,
        to_token=TOKEN_OUT,
        amount_in="1.5",
        amount_in_raw=1_500_000,
        idempotency_key="swap-1",
        from_token_is_native=True,
        wrapped_from_token="0x4200000000000000000000000000000000000006",
    )


def _response(*, include_typed_data: bool) -> dict[str, object]:
    response: dict[str, object] = {
        "id": "cow-quote-1",
        "spender": SPENDER,
        "quote": {
            "sellToken": TOKEN_IN,
            "buyToken": TOKEN_OUT,
            "sellAmount": "1500000",
            "buyAmount": "1000000",
            "validTo": int((datetime.now(tz=UTC) + timedelta(minutes=5)).timestamp()),
        },
    }
    if include_typed_data:
        response["typedData"] = {"domain": {"chainId": 8453}, "message": {"sellToken": TOKEN_IN}}
    return response
