import pytest
from mercury.models.erc20 import ZERO_ADDRESS
from mercury.models.swaps import SwapExecutionType, SwapQuoteRequest
from mercury.swaps.base import SwapProviderError
from mercury.swaps.uniswap import UniswapProvider

TOKEN_IN = "0x000000000000000000000000000000000000cafE"
TOKEN_OUT = "0x000000000000000000000000000000000000dEaD"
WALLET = "0x000000000000000000000000000000000000bEEF"
SPENDER = "0x0000000000000000000000000000000000000002"
SWAP_TO = "0x0000000000000000000000000000000000000003"


def test_uniswap_quote_and_build_normalize_mocked_api() -> None:
    provider = UniswapProvider(http_client=FakeHttpClient())

    quote = provider.get_quote(_request())
    execution = provider.build_execution(quote)

    assert quote.route.spender_address == SPENDER
    assert quote.expected_amount_out_raw == 1_000_000
    assert execution.execution_type == SwapExecutionType.EVM_TRANSACTION
    assert execution.transaction is not None
    assert execution.transaction.to == SWAP_TO


def test_uniswap_rejects_native_sell_with_clear_error() -> None:
    provider = UniswapProvider(http_client=FakeHttpClient())

    with pytest.raises(SwapProviderError, match="native token"):
        provider.get_quote(_native_request())


class FakeHttpClient:
    def post_json(
        self,
        path: str,
        *,
        payload: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        if path == "/v1/quote":
            return {
                "quote": {
                    "requestId": "uni-quote-1",
                    "tokenInChainId": 8453,
                    "tokenOutChainId": 8453,
                    "tokenIn": TOKEN_IN,
                    "tokenOut": TOKEN_OUT,
                    "amount": "1500000",
                    "amountOut": "1000000",
                    "minAmountOut": "990000",
                    "permit2Address": SPENDER,
                }
            }
        if path == "/v1/swap":
            return {"transaction": {"to": SWAP_TO, "data": "0x1234", "value": "0"}}
        raise AssertionError(f"unexpected path {path}")

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        raise AssertionError("Uniswap quote test should not GET")


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
