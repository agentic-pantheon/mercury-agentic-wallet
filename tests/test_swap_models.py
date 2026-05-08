from datetime import UTC, datetime, timedelta

import pytest
from mercury.models.addresses import normalize_evm_address
from mercury.models.erc20 import ZERO_ADDRESS
from mercury.models.native_tokens import NATIVE_FROM_TOKEN_SENTINEL_ALIAS
from mercury.models.swaps import (
    SwapIntent,
    SwapProviderName,
    SwapQuote,
    SwapQuoteRequest,
    SwapRoute,
    SwapRouteKind,
)
from pydantic import ValidationError

TOKEN_IN = "0x000000000000000000000000000000000000cafE"
TOKEN_OUT = "0x000000000000000000000000000000000000dEaD"
WALLET = "0x000000000000000000000000000000000000bEEF"
SPENDER = "0x0000000000000000000000000000000000000002"


def test_swap_intent_native_zero_from_token_normalized_canonically() -> None:
    canonical_zero = normalize_evm_address(ZERO_ADDRESS)
    intent = SwapIntent(
        wallet_id="primary",
        chain="ethereum",
        from_token=ZERO_ADDRESS.lower(),
        to_token=TOKEN_OUT,
        amount_in="1",
        idempotency_key="swap-native",
    )
    assert intent.from_token == canonical_zero


def test_swap_intent_native_alias_from_token_normalized_canonically() -> None:
    canonical_zero = normalize_evm_address(ZERO_ADDRESS)
    intent = SwapIntent(
        wallet_id="primary",
        chain="ethereum",
        from_token=NATIVE_FROM_TOKEN_SENTINEL_ALIAS.lower(),
        to_token=TOKEN_OUT,
        amount_in="1",
        idempotency_key="swap-native-alias",
    )
    assert intent.from_token == canonical_zero


def test_swap_intent_to_token_sentinel_alias_is_plain_checksum_address() -> None:
    intent = SwapIntent(
        wallet_id="primary",
        chain="ethereum",
        from_token=TOKEN_IN,
        to_token=NATIVE_FROM_TOKEN_SENTINEL_ALIAS.lower(),
        amount_in="1",
        idempotency_key="swap-to-alias",
    )
    assert intent.to_token == NATIVE_FROM_TOKEN_SENTINEL_ALIAS
    assert intent.from_token != normalize_evm_address(ZERO_ADDRESS)


def test_swap_quote_request_native_from_token_normalized() -> None:
    canonical_zero = normalize_evm_address(ZERO_ADDRESS)
    req = SwapQuoteRequest(
        wallet_id="primary",
        wallet_address=WALLET,
        chain="ethereum",
        chain_id=1,
        from_token=ZERO_ADDRESS.lower(),
        to_token=TOKEN_OUT,
        amount_in="1",
        amount_in_raw=1_000_000,
        idempotency_key="quote-native",
    )
    assert req.from_token == canonical_zero


def test_swap_quote_request_native_alias_from_token_normalized() -> None:
    canonical_zero = normalize_evm_address(ZERO_ADDRESS)
    req = SwapQuoteRequest(
        wallet_id="primary",
        wallet_address=WALLET,
        chain="ethereum",
        chain_id=1,
        from_token=NATIVE_FROM_TOKEN_SENTINEL_ALIAS.lower(),
        to_token=TOKEN_OUT,
        amount_in="1",
        amount_in_raw=1_000_000,
        idempotency_key="quote-alias",
    )
    assert req.from_token == canonical_zero


def test_swap_intent_normalizes_addresses_and_provider() -> None:
    intent = SwapIntent(
        wallet_id="primary",
        chain="BASE",
        from_token=TOKEN_IN.lower(),
        to_token=TOKEN_OUT.lower(),
        amount_in="1.5",
        max_slippage_bps=50,
        provider_preference=SwapProviderName.LIFI,
        idempotency_key="swap-1",
    )

    assert intent.chain == "base"
    assert intent.from_token == TOKEN_IN
    assert intent.provider_preference == SwapProviderName.LIFI


def test_swap_intent_rejects_invalid_slippage() -> None:
    with pytest.raises(ValidationError):
        SwapIntent(
            wallet_id="primary",
            chain="base",
            from_token=TOKEN_IN,
            to_token=TOKEN_OUT,
            amount_in="1",
            max_slippage_bps=10_001,
            idempotency_key="swap-1",
        )


def test_swap_quote_validates_provider_response_consistency() -> None:
    request = _request()

    quote = SwapQuote(
        provider=SwapProviderName.LIFI,
        request=request,
        route=SwapRoute(
            provider=SwapProviderName.LIFI,
            route_id="route-1",
            from_chain_id=8453,
            to_chain_id=8453,
            from_token=TOKEN_IN,
            to_token=TOKEN_OUT,
            spender_address=SPENDER,
        ),
        amount_in_raw=1_500_000,
        expected_amount_out_raw=1_000_000,
        slippage_bps=50,
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        recipient_address=WALLET,
    )

    assert quote.route.spender_address == SPENDER


def test_swap_quote_accepts_bridge_when_destination_matches_request() -> None:
    request = SwapQuoteRequest(
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
        to_chain="ethereum",
        to_chain_id=1,
    )
    quote = SwapQuote(
        provider=SwapProviderName.LIFI,
        request=request,
        route=SwapRoute(
            provider=SwapProviderName.LIFI,
            route_id="r1",
            route_kind=SwapRouteKind.BRIDGE,
            from_chain_id=8453,
            to_chain_id=1,
            from_token=TOKEN_IN,
            to_token=TOKEN_OUT,
            spender_address=SPENDER,
        ),
        amount_in_raw=1_500_000,
        expected_amount_out_raw=1_000_000,
        recipient_address=WALLET,
    )
    assert quote.route.route_kind == SwapRouteKind.BRIDGE


def test_swap_quote_rejects_destination_mismatch_for_bridge_request() -> None:
    request = SwapQuoteRequest(
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
        to_chain_id=1,
    )
    with pytest.raises(ValidationError, match="destination chain"):
        SwapQuote(
            provider=SwapProviderName.LIFI,
            request=request,
            route=SwapRoute(
                provider=SwapProviderName.LIFI,
                route_id="r1",
                route_kind=SwapRouteKind.BRIDGE,
                from_chain_id=8453,
                to_chain_id=10,
                from_token=TOKEN_IN,
                to_token=TOKEN_OUT,
                spender_address=SPENDER,
            ),
            amount_in_raw=1_500_000,
            expected_amount_out_raw=1_000_000,
            recipient_address=WALLET,
        )


def test_swap_quote_rejects_chain_mismatch() -> None:
    request = _request()

    with pytest.raises(ValidationError, match="source chain"):
        SwapQuote(
            provider=SwapProviderName.LIFI,
            request=request,
            route=SwapRoute(
                provider=SwapProviderName.LIFI,
                route_id="route-1",
                from_chain_id=1,
                to_chain_id=1,
                from_token=TOKEN_IN,
                to_token=TOKEN_OUT,
                spender_address=SPENDER,
            ),
            amount_in_raw=1_500_000,
            expected_amount_out_raw=1_000_000,
            recipient_address=WALLET,
        )


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
