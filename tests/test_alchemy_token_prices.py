"""Tests for Alchemy token prices client, validation, and tool wiring."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from mercury.alchemy.prices import (
    AlchemyTokenPricesClient,
    AlchemyTokenPricesValidationError,
    normalize_token_price_rows,
)
from mercury.custody.oneclaw import FakeSecretStore
from mercury.tools.token_prices import AlchemyPricesToolDeps, create_alchemy_token_prices_tool, get_token_prices


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


def test_normalize_token_price_rows_handles_error_field() -> None:
    raw = [
        {
            "network": "eth-mainnet",
            "address": "0x0000000000000000000000000000000000000001",
            "prices": [{"currency": "usd", "value": "2.5", "lastUpdatedAt": "t"}],
        },
        {"network": "base-mainnet", "address": "0x2", "error": "boom", "prices": []},
    ]
    mercury_by_net = {"eth-mainnet": "ethereum", "base-mainnet": "base"}
    rows = normalize_token_price_rows(raw, mercury_chain_by_network=mercury_by_net)
    assert rows[0]["error"] is None
    assert rows[0]["mercury_chain"] == "ethereum"
    assert rows[1]["error"] == "boom"


def test_client_rejects_more_than_25_entries() -> None:
    store = FakeSecretStore({"k": "fake-key"})
    client = AlchemyTokenPricesClient(secret_store=store, api_key_secret_path="k", http=_FakeJsonHttp({}))
    entries = [("ethereum", "0x0000000000000000000000000000000000000001")] * 26
    with pytest.raises(AlchemyTokenPricesValidationError, match="25"):
        client.fetch_prices_by_address(entries)


def test_client_rejects_more_than_three_networks() -> None:
    store = FakeSecretStore({"k": "fake-key"})
    client = AlchemyTokenPricesClient(secret_store=store, api_key_secret_path="k", http=_FakeJsonHttp({}))
    entries = [
        ("ethereum", "0x0000000000000000000000000000000000000001"),
        ("base", "0x0000000000000000000000000000000000000002"),
        ("arbitrum", "0x0000000000000000000000000000000000000003"),
        ("optimism", "0x0000000000000000000000000000000000000004"),
    ]
    with pytest.raises(AlchemyTokenPricesValidationError, match="3"):
        client.fetch_prices_by_address(entries)


def test_client_posts_normalized_addresses_and_maps_response() -> None:
    http = _FakeJsonHttp(
        {
            "data": [
                {
                    "network": "base-mainnet",
                    "address": "0x000000000000000000000000000000000000caFe",
                    "prices": [{"currency": "usd", "value": "1", "lastUpdatedAt": None}],
                }
            ]
        }
    )
    store = FakeSecretStore({"mercury/apis/alchemy": "demo-api-key"})
    client = AlchemyTokenPricesClient(
        secret_store=store,
        api_key_secret_path="mercury/apis/alchemy",
        http=http,
    )
    out = client.fetch_prices_by_address([("base", "0x000000000000000000000000000000000000cafE")])
    assert len(http.posts) == 1
    assert http.posts[0]["path"] == "prices/v1/demo-api-key/tokens/by-address"
    addrs = http.posts[0]["payload"]["addresses"]
    assert addrs == [{"network": "base-mainnet", "address": "0x000000000000000000000000000000000000cafE"}]
    assert out["tokens"][0]["prices"][0]["value"] == "1"
    assert out["tokens"][0]["mercury_chain"] == "base"


class _StubPricesClient:
    def fetch_prices_by_address(self, entries: list[tuple[str, str]]) -> dict[str, Any]:
        assert len(entries) == 1
        assert entries[0][0] == "base"
        return {
            "tokens": [
                {
                    "network": "base-mainnet",
                    "mercury_chain": "base",
                    "address": "0x000000000000000000000000000000000000cAfE",
                    "prices": [],
                    "error": None,
                }
            ]
        }


def test_get_token_prices_tool_accepts_list_of_dicts() -> None:
    tool = create_alchemy_token_prices_tool(
        AlchemyPricesToolDeps(secret_store=FakeSecretStore({}), api_key_secret_path="x"),
        client=_StubPricesClient(),
    )
    raw = tool.invoke(
        {
            "tokens": [{"chain": "base", "token_address": "0x000000000000000000000000000000000000cafE"}],
        }
    )
    assert raw["tokens"][0]["mercury_chain"] == "base"


def test_get_token_prices_helper_validates_batch() -> None:
    many = [{"chain": "ethereum", "token_address": "0x0000000000000000000000000000000000000001"}] * 26
    with pytest.raises(ValidationError, match="25"):
        get_token_prices(tokens=many, client=_StubPricesClient())
