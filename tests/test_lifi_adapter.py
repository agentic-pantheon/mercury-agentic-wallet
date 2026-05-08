from typing import Any, cast

from mercury.models.addresses import normalize_evm_address
from mercury.models.erc20 import ZERO_ADDRESS
from mercury.models.swaps import SwapExecutionType, SwapProviderName, SwapQuoteRequest
from mercury.swaps.lifi import LiFiProvider

TOKEN_IN = "0x000000000000000000000000000000000000cafE"
TOKEN_OUT = "0x000000000000000000000000000000000000dEaD"
WALLET = "0x000000000000000000000000000000000000bEEF"
SPENDER = "0x0000000000000000000000000000000000000002"
SWAP_TO = "0x0000000000000000000000000000000000000003"


def test_lifi_quote_response_normalizes_route_and_spender() -> None:
    http = FakeHttpClient(_lifi_response())
    provider = LiFiProvider(http_client=http)

    quote = provider.get_quote(_request())

    assert quote.provider == SwapProviderName.LIFI
    assert quote.route.from_chain_id == 8453
    assert quote.route.spender_address == SPENDER
    assert quote.expected_amount_out_raw == 1_000_000
    params = cast(dict[str, Any], http.get_requests[0]["params"])
    assert params["fromAmount"] == "1500000"


def test_lifi_build_execution_produces_evm_payload() -> None:
    provider = LiFiProvider(http_client=FakeHttpClient(_lifi_response()))
    quote = provider.get_quote(_request())

    execution = provider.build_execution(quote)

    assert execution.execution_type == SwapExecutionType.EVM_TRANSACTION
    assert execution.transaction is not None
    assert execution.transaction.to == SWAP_TO
    assert execution.transaction.data == "0x1234"


def test_lifi_native_quote_sends_canonical_native_as_from_token_param() -> None:
    http = FakeHttpClient(_lifi_response())
    provider = LiFiProvider(http_client=http)

    provider.get_quote(_native_request())

    params = cast(dict[str, Any], http.get_requests[0]["params"])
    assert params["fromToken"] == normalize_evm_address(ZERO_ADDRESS)


def test_lifi_native_quote_keeps_route_from_token_matching_request_despite_wrapped_action() -> (
    None
):
    body = _lifi_response()
    action = cast(dict[str, Any], body["action"])
    action["fromToken"] = "0x4200000000000000000000000000000000000006"
    http = FakeHttpClient(body)
    provider = LiFiProvider(http_client=http)

    req = _native_request()
    quote = provider.get_quote(req)

    assert quote.route.from_token == req.from_token


def test_lifi_build_execution_preserves_nonzero_transaction_value() -> None:
    raw = _lifi_response()
    tr = cast(dict[str, Any], raw["transactionRequest"])
    tr["value"] = "5000000000000000"
    provider = LiFiProvider(http_client=FakeHttpClient(raw))
    quote = provider.get_quote(_native_request())

    execution = provider.build_execution(quote)

    assert execution.transaction is not None
    assert execution.transaction.value_wei == 5_000_000_000_000_000


class FakeHttpClient:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.get_requests: list[dict[str, object]] = []

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.get_requests.append({"path": path, "params": params or {}, "headers": headers or {}})
        return self._response

    def post_json(
        self,
        path: str,
        *,
        payload: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        raise AssertionError("LiFi quote test should not POST")


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
        max_slippage_bps=50,
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
        max_slippage_bps=50,
        idempotency_key="swap-1",
        from_token_is_native=True,
        wrapped_from_token="0x4200000000000000000000000000000000000006",
    )


def _lifi_response() -> dict[str, object]:
    return {
        "id": "lifi-route-1",
        "tool": "lifi",
        "action": {
            "fromChainId": 8453,
            "toChainId": 8453,
            "fromToken": TOKEN_IN,
            "toToken": TOKEN_OUT,
        },
        "estimate": {
            "fromAmount": "1500000",
            "toAmount": "1000000",
            "toAmountMin": "990000",
            "approvalAddress": SPENDER,
        },
        "transactionRequest": {
            "to": SWAP_TO,
            "data": "0x1234",
            "value": "0",
        },
    }
