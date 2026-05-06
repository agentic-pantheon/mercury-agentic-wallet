"""Tests for Alchemy transfer history client, validation, registry, and tool wiring."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from mercury.alchemy.transfers import (
    AlchemyTransfersClient,
    build_asset_transfer_params,
    normalize_asset_transfer_rows,
    normalize_block_param,
)
from mercury.custody.oneclaw import FakeSecretStore
from mercury.graph.intents import TransferHistoryIntent, parse_readonly_intent
from mercury.graph.nodes import resolve_chain
from mercury.graph.state import MercuryState
from mercury.tools.registry import ReadOnlyToolRegistry
from mercury.tools.schemas import TransferHistoryToolInput
from mercury.tools.transfer_history import (
    AlchemyTransfersToolDeps,
    create_alchemy_transfer_history_tool,
    get_transfer_history,
)


class _FakeJsonHttp:
    """Minimal JsonHttpClient stub."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
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
        if not self.responses:
            msg = "no stub responses left"
            raise RuntimeError(msg)
        return self.responses.pop(0)


WALLET = "0x000000000000000000000000000000000000dEaD"


def test_build_asset_transfer_params_direction_and_order() -> None:
    inc = build_asset_transfer_params(
        wallet_address=WALLET,
        leg="incoming",
        categories=["erc20"],
        max_count=10,
        page_key="abc",
        with_metadata=True,
        exclude_zero_value=False,
    )
    assert inc["toAddress"] == WALLET
    assert "fromAddress" not in inc
    assert inc["order"] == "desc"
    assert inc["maxCount"] == "0xa"
    assert inc["pageKey"] == "abc"
    assert inc["withMetadata"] is True
    assert inc["excludeZeroValue"] is False

    out = build_asset_transfer_params(
        wallet_address=WALLET,
        leg="outgoing",
        categories=["external"],
        from_block=123,
        to_block="latest",
        max_count=1000,
        page_key=None,
        with_metadata=False,
        exclude_zero_value=True,
    )
    assert out["fromAddress"] == WALLET
    assert out["fromBlock"] == "0x7b"
    assert out["toBlock"] == "latest"


def test_normalize_block_param_decimal_string() -> None:
    assert normalize_block_param("10") == "0xa"


def test_normalize_asset_transfer_rows_stable_shape() -> None:
    raw = [
        {
            "blockNum": "0x10",
            "hash": "0xabc",
            "from": WALLET,
            "to": "0x0000000000000000000000000000000000000002",
            "category": "erc20",
            "asset": "USDC",
            "value": 1.5,
            "rawContract": {"address": "0x0", "decimal": "6"},
            "metadata": {"blockTimestamp": "1"},
        }
    ]
    rows = normalize_asset_transfer_rows(raw, network="eth-mainnet")
    assert len(rows) == 1
    r = rows[0]
    assert r["block_num"] == 16
    assert r["from_address"] == WALLET
    assert r["category"] == "erc20"
    assert r["mercury_chain"] == "ethereum"
    assert r["network"] == "eth-mainnet"
    assert isinstance(r["raw_contract"], dict)


def test_transfer_tool_input_rejects_bad_category() -> None:
    with pytest.raises(ValidationError):
        TransferHistoryToolInput.model_validate(
            {
                "chain": "ethereum",
                "wallet_address": WALLET,
                "categories": ["fakecat"],
            }
        )


def test_transfer_tool_input_direction_aliases_and_page_key_cleared_for_both() -> None:
    p = TransferHistoryToolInput.model_validate(
        {
            "chain": "ethereum",
            "wallet_address": WALLET,
            "direction": "in",
            "page_key": " k ",
        }
    )
    assert p.direction == "incoming"
    assert p.page_key == "k"

    p2 = TransferHistoryToolInput.model_validate(
        {
            "chain": "ethereum",
            "wallet_address": WALLET,
            "direction": "both",
            "page_key": "should-drop",
        }
    )
    assert p2.page_key is None


def test_transfer_intent_max_count_limits() -> None:
    with pytest.raises(ValidationError):
        TransferHistoryIntent.model_validate(
            {
                "kind": "transfer_history",
                "wallet_address": WALLET,
                "chain": "ethereum",
                "max_count": 1001,
            }
        )


def test_parse_transfer_history_accepts_alias_kind() -> None:
    payload = parse_readonly_intent(
        {
            "kind": "get_transfer_history",
            "wallet_address": WALLET,
            "chain": "base",
            "direction": "out",
        }
    )
    assert isinstance(payload, TransferHistoryIntent)
    assert payload.direction == "outgoing"


def test_readonly_registry_omits_transfer_tool_without_alchemy_deps() -> None:
    factory = MagicMock()
    reg = ReadOnlyToolRegistry.from_provider_factory(factory, alchemy_transfers=None)
    assert "get_transfer_history" not in reg.names()


def test_readonly_registry_includes_transfer_when_configured() -> None:
    factory = MagicMock()
    reg = ReadOnlyToolRegistry.from_provider_factory(
        factory,
        alchemy_transfers=AlchemyTransfersToolDeps(
            secret_store=FakeSecretStore({"k": "x"}),
            api_key_secret_path="k",
        ),
    )
    assert "get_transfer_history" in reg.names()


def test_client_posts_rpc_and_returns_page_key() -> None:
    rpc_result = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "transfers": [
                {
                    "blockNum": "0x1",
                    "hash": "0xh1",
                    "from": WALLET,
                    "to": "0x0000000000000000000000000000000000000002",
                    "category": "external",
                }
            ],
            "pageKey": "next",
        },
    }
    http = _FakeJsonHttp([rpc_result])
    store = FakeSecretStore({"mercury/apis/alchemy": "demo-api-key"})
    client = AlchemyTransfersClient(
        secret_store=store,
        api_key_secret_path="mercury/apis/alchemy",
        http=http,
    )
    out = client.fetch_transfers_for_wallet(
        mercury_chain="ethereum",
        wallet_address=WALLET,
        direction="outgoing",
        categories=["external"],
        from_block=None,
        to_block=None,
        max_count=5,
        page_key=None,
        with_metadata=True,
        exclude_zero_value=True,
    )
    assert len(http.posts) == 1
    assert http.posts[0]["path"] == "demo-api-key"
    assert http.posts[0]["payload"]["method"] == "alchemy_getAssetTransfers"
    inner = http.posts[0]["payload"]["params"][0]
    assert inner["fromAddress"] == WALLET
    assert out["page_key"] == "next"
    assert out["transfers"][0]["block_num"] == 1


def test_client_merges_both_directions() -> None:
    row_out = {
        "blockNum": "0x2",
        "hash": "0xb",
        "from": WALLET,
        "to": "0x0000000000000000000000000000000000000003",
        "category": "external",
    }
    row_in = {
        "blockNum": "0x3",
        "hash": "0xc",
        "from": "0x0000000000000000000000000000000000000004",
        "to": WALLET,
        "category": "erc20",
    }
    http = _FakeJsonHttp(
        [
            {"jsonrpc": "2.0", "id": 1, "result": {"transfers": [row_out], "pageKey": "p1"}},
            {"jsonrpc": "2.0", "id": 1, "result": {"transfers": [row_in], "pageKey": "p2"}},
        ]
    )
    client = AlchemyTransfersClient(
        secret_store=FakeSecretStore({"k": "key"}),
        api_key_secret_path="k",
        http=http,
    )
    out = client.fetch_transfers_for_wallet(
        mercury_chain="base",
        wallet_address=WALLET,
        direction="both",
        categories=["external", "erc20"],
        from_block=None,
        to_block=None,
        max_count=10,
        page_key="ignored",
        with_metadata=False,
        exclude_zero_value=False,
    )
    assert len(http.posts) == 2
    assert out["page_key"] is None
    assert len(out["transfers"]) == 2
    assert out["transfers"][0]["block_num"] == 3


def test_get_transfer_history_tool_invokes_stub() -> None:
    class _Stub:
        def fetch_transfers_for_wallet(
            self,
            *,
            mercury_chain: str,
            wallet_address: str,
            direction: str,
            categories: list[str],
            from_block: str | int | None,
            to_block: str | int | None,
            max_count: int,
            page_key: str | None,
            with_metadata: bool,
            exclude_zero_value: bool,
        ) -> dict[str, Any]:
            assert mercury_chain == "ethereum"
            assert wallet_address == WALLET
            assert direction == "incoming"
            assert "erc20" in categories

            return {
                "wallet_address": wallet_address,
                "mercury_chain": mercury_chain,
                "network": "eth-mainnet",
                "transfers": [],
                "page_key": None,
            }

    tool = create_alchemy_transfer_history_tool(
        AlchemyTransfersToolDeps(secret_store=FakeSecretStore({}), api_key_secret_path="k"),
        client=_Stub(),  # type: ignore[arg-type]
    )
    result = tool.invoke(
        {
            "chain": "ethereum",
            "wallet_address": WALLET,
            "direction": "in",
        }
    )
    assert result["network"] == "eth-mainnet"


def test_get_transfer_history_helper_uses_client() -> None:
    captured: dict[str, Any] = {}

    class _Stub:
        def fetch_transfers_for_wallet(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {
                "wallet_address": WALLET,
                "mercury_chain": "ethereum",
                "network": "eth-mainnet",
                "transfers": [],
                "page_key": "pk",
            }

    out = get_transfer_history(
        chain="ethereum",
        wallet_address=WALLET,
        direction="incoming",
        page_key="next",
        client=_Stub(),  # type: ignore[arg-type]
    )
    assert captured["page_key"] == "next"
    assert out["page_key"] == "pk"
    assert captured["direction"] == "incoming"


def test_monad_chain_rejected_for_transfer_resolve() -> None:
    parsed = parse_readonly_intent(
        {
            "kind": "transfer_history",
            "wallet_address": WALLET,
            "chain": "monad",
        }
    )
    state: MercuryState = {"parsed_intent": parsed.model_dump(mode="json"), "raw_input": {}}
    upd = resolve_chain(state)
    assert upd.get("error") is not None
