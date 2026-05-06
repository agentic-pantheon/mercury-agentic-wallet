"""Tests for Alchemy portfolio client, validation, registry, and tool wiring."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from mercury.alchemy.portfolio import (
    AlchemyPortfolioClient,
    AlchemyPortfolioValidationError,
    normalize_portfolio_token_rows,
)
from mercury.custody.oneclaw import FakeSecretStore
from mercury.graph.intents import PortfolioTokensIntent, parse_readonly_intent
from mercury.tools.portfolio_tokens import (
    AlchemyPortfolioToolDeps,
    create_alchemy_portfolio_tokens_tool,
    get_portfolio_tokens,
)
from mercury.tools.registry import ReadOnlyToolRegistry
from mercury.tools.schemas import PortfolioTokensToolInput


class _FakeJsonHttp:
    """Minimal JsonHttpClient stub."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.posts: list[dict[str, Any]] = []

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def post_json(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.posts.append({"path": path, "payload": payload})
        return self.response


WALLET = "0x000000000000000000000000000000000000dEaD"


def test_readonly_registry_omits_portfolio_tool_without_alchemy_deps() -> None:
    factory = MagicMock()
    reg = ReadOnlyToolRegistry.from_provider_factory(
        factory,
        alchemy_prices=None,
        alchemy_portfolio=None,
    )
    assert "get_portfolio_tokens" not in reg.names()


def test_readonly_registry_includes_portfolio_when_configured() -> None:
    factory = MagicMock()
    reg = ReadOnlyToolRegistry.from_provider_factory(
        factory,
        alchemy_portfolio=AlchemyPortfolioToolDeps(
            secret_store=FakeSecretStore({"k": "x"}),
            api_key_secret_path="k",
        ),
    )
    assert "get_portfolio_tokens" in reg.names()


def test_normalize_portfolio_rows_preserves_keys() -> None:
    raw = [
        {
            "address": WALLET,
            "network": "eth-mainnet",
            "tokenAddress": None,
            "tokenBalance": "1000",
            "tokenMetadata": {"decimals": 18, "name": "Ether", "symbol": "ETH", "logo": None},
            "tokenPrices": [{"currency": "usd", "value": "1", "lastUpdatedAt": "t"}],
            "error": None,
        },
        {
            "address": WALLET,
            "network": "base-mainnet",
            "tokenAddress": "0x000000000000000000000000000000000000cafE",
            "tokenBalance": "42",
            "error": "rpc hiccup",
        },
    ]
    out = normalize_portfolio_token_rows(raw, wallet_address=WALLET)
    assert out[0]["mercury_chain"] == "ethereum"
    assert out[0]["token_address"] is None
    assert out[0]["balance"] == "1000"
    assert out[0]["metadata"]["symbol"] == "ETH"
    assert out[1]["error"] == "rpc hiccup"
    assert out[1]["token_address"] == "0x000000000000000000000000000000000000cafE"


def test_client_posts_portfolio_payload_and_maps_page_key() -> None:
    http = _FakeJsonHttp(
        {
            "data": {
                "tokens": [],
                "pageKey": "next-page",
            }
        }
    )
    store = FakeSecretStore({"mercury/apis/alchemy": "demo-api-key"})
    client = AlchemyPortfolioClient(
        secret_store=store,
        api_key_secret_path="mercury/apis/alchemy",
        http=http,
    )
    result = client.fetch_tokens_for_wallet(
        wallet_address=WALLET,
        mercury_chains=["ethereum"],
        with_metadata=True,
        with_prices=True,
        include_native_tokens=True,
        include_erc20_tokens=True,
        page_key=None,
    )
    assert len(http.posts) == 1
    assert http.posts[0]["path"] == "data/v1/demo-api-key/assets/tokens/by-address"
    assert http.posts[0]["payload"]["addresses"] == [
        {"address": WALLET, "networks": ["eth-mainnet"]}
    ]
    assert http.posts[0]["payload"]["withMetadata"] is True
    assert result["page_key"] == "next-page"


def test_client_passes_page_key_through() -> None:
    http = _FakeJsonHttp({"data": {"tokens": []}})
    store = FakeSecretStore({"k": "key"})
    client = AlchemyPortfolioClient(secret_store=store, api_key_secret_path="k", http=http)
    client.fetch_tokens_for_wallet(
        wallet_address=WALLET,
        mercury_chains=["base"],
        with_metadata=False,
        with_prices=False,
        include_native_tokens=True,
        include_erc20_tokens=True,
        page_key="abc",
    )
    assert http.posts[0]["payload"]["pageKey"] == "abc"


def test_client_rejects_more_than_five_network_entries() -> None:
    store = FakeSecretStore({"k": "fake-key"})
    client = AlchemyPortfolioClient(
        secret_store=store,
        api_key_secret_path="k",
        http=_FakeJsonHttp({}),
    )
    with pytest.raises(AlchemyPortfolioValidationError, match="5"):
        client.fetch_tokens_for_wallet(
            wallet_address=WALLET,
            mercury_chains=["ethereum"] * 6,
            with_metadata=True,
            with_prices=True,
            include_native_tokens=True,
            include_erc20_tokens=True,
            page_key=None,
        )


def test_parse_portfolio_accepts_four_alchemy_mapped_chains() -> None:
    parsed = parse_readonly_intent(
        {
            "kind": "portfolio_tokens",
            "wallet_address": WALLET,
            "chains": ["ethereum", "base", "arbitrum", "optimism"],
        }
    )
    assert isinstance(parsed, PortfolioTokensIntent)
    assert len(parsed.chains or []) == 4


def test_portfolio_tool_input_rejects_long_chain_list() -> None:
    with pytest.raises(ValidationError):
        PortfolioTokensToolInput.model_validate(
            {
                "wallet_address": WALLET,
                "chains": ["ethereum", "base", "arbitrum", "optimism", "ethereum", "monad"],
            }
        )


def test_parse_portfolio_rejects_chains_and_networks_together() -> None:
    from mercury.graph.intents import UnsupportedIntentError

    with pytest.raises(UnsupportedIntentError):
        parse_readonly_intent(
            {
                "kind": "portfolio_tokens",
                "wallet_address": WALLET,
                "chains": ["ethereum"],
                "networks": ["base-mainnet"],
            }
        )


def test_parse_networks_alias_orders_chains() -> None:
    parsed = parse_readonly_intent(
        {
            "kind": "portfolio_tokens",
            "wallet_address": WALLET,
            "networks": ["base-mainnet", "eth-mainnet"],
        }
    )
    assert isinstance(parsed, PortfolioTokensIntent)
    assert sorted(parsed.chains or []) == ["base", "ethereum"]


class _StubPortfolioClient:
    def fetch_tokens_for_wallet(
        self,
        *,
        wallet_address: str,
        mercury_chains: list[str],
        with_metadata: bool,
        with_prices: bool,
        include_native_tokens: bool,
        include_erc20_tokens: bool,
        page_key: str | None,
    ) -> dict[str, Any]:
        assert wallet_address == WALLET
        assert mercury_chains == ["ethereum"]
        assert page_key is None
        return {
            "wallet_address": wallet_address,
            "tokens": [
                {
                    "wallet_address": wallet_address,
                    "network": "eth-mainnet",
                    "mercury_chain": "ethereum",
                    "token_address": None,
                    "balance": "1",
                    "metadata": {"decimals": 18, "logo": None, "name": "x", "symbol": "ETH"},
                    "prices": [],
                    "error": None,
                }
            ],
            "page_key": None,
        }


def test_get_portfolio_tokens_tool_invokes_stub() -> None:
    tool = create_alchemy_portfolio_tokens_tool(
        AlchemyPortfolioToolDeps(secret_store=FakeSecretStore({}), api_key_secret_path="x"),
        client=_StubPortfolioClient(),
    )
    raw = tool.invoke({"wallet_address": WALLET, "chains": ["ethereum"]})
    assert raw["tokens"][0]["mercury_chain"] == "ethereum"


def test_get_portfolio_helper_validates_chain_count() -> None:
    with pytest.raises(ValidationError):
        get_portfolio_tokens(
            wallet_address=WALLET,
            chains=["ethereum", "base", "arbitrum", "optimism", "monad", "ethereum"],
            client=_StubPortfolioClient(),
        )
