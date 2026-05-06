"""Tests for Alchemy ADDRESS_ACTIVITY normalization, filter, dedupe, and graph."""

from __future__ import annotations

from mercury.graph.webhook_graph import compiled_alchemy_webhook_graph
from mercury.webhooks.alchemy_dedupe import AlchemyWebhookDedupeStore
from mercury.webhooks.alchemy_handler import (
    ADDRESS_ACTIVITY,
    filter_incoming_for_watched,
    merge_watched_addresses,
    normalize_activity_rows,
    row_to_alert,
)

_ACTIVITY_ROW = {
    "blockNum": "0xdf34a3",
    "hash": "0x7a4a39da2a3fa1fc2ef88fd1eaea070286ed2aba21e0419dcfb6d5c5d9f02a72",
    "fromAddress": "0x503828976d22510aad0201ac7ec88293211d23da",
    "toAddress": "0xbe3f4b43db5eb49d1f48f53443b9abce45da3b79",
    "value": 293.092129,
    "erc721TokenId": None,
    "erc1155Metadata": None,
    "asset": "USDC",
    "category": "token",
    "rawContract": {
        "rawValue": "0x00",
        "address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "decimals": 6,
    },
    "typeTraceAddress": None,
    "log": {
        "address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "logIndex": "0x6e",
        "transactionHash": "0x7a4a39da2a3fa1fc2ef88fd1eaea070286ed2aba21e0419dcfb6d5c5d9f02a72",
        "removed": False,
    },
}


def test_row_to_alert_normalizes_addresses_and_fields() -> None:
    alert = row_to_alert(network="ETH_MAINNET", row=_ACTIVITY_ROW)
    assert alert["network"] == "ETH_MAINNET"
    assert alert["tx_hash"] == "0x7a4a39da2a3fa1fc2ef88fd1eaea070286ed2aba21e0419dcfb6d5c5d9f02a72"
    assert alert["from_address"] == "0x503828976d22510aad0201ac7ec88293211d23da"
    assert alert["to_address"] == "0xbe3f4b43db5eb49d1f48f53443b9abce45da3b79"
    assert alert["asset"] == "USDC"
    assert alert["category"] == "token"
    assert alert["value"] == 293.092129
    assert alert["contract_address"] == "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    assert alert["removed"] is False
    assert alert["log_index"] == "0x6e"


def test_filter_incoming_with_watch_list_keeps_matching_to_address() -> None:
    alerts = normalize_activity_rows(network="ETH_MAINNET", activity=[_ACTIVITY_ROW])
    watched = ["0xbe3f4b43db5eb49d1f48f53443b9abce45da3b79"]
    filtered = filter_incoming_for_watched(alerts, watched)
    assert len(filtered) == 1
    filtered_mismatch = filter_incoming_for_watched(
        alerts,
        ["0x0000000000000000000000000000000000000001"],
    )
    assert filtered_mismatch == []


def test_filter_incoming_empty_watch_passes_all() -> None:
    alerts = normalize_activity_rows(
        network="ETH_MAINNET",
        activity=[_ACTIVITY_ROW, dict(_ACTIVITY_ROW)],
    )
    assert len(filter_incoming_for_watched(alerts, [])) == 2


def test_merge_watched_addresses_query_and_metadata() -> None:
    payload = {
        "metadata": {
            "watched_addresses": ["0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"],
        }
    }
    merged = merge_watched_addresses("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", payload)
    assert merged == [
        "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]


def test_webhook_graph_dedupes_retries() -> None:
    store = AlchemyWebhookDedupeStore(ttl_seconds=3600.0)
    graph = compiled_alchemy_webhook_graph(store)
    payload = {
        "webhookId": "wh_1",
        "id": "evt_1",
        "type": ADDRESS_ACTIVITY,
        "event": {"network": "ETH_MAINNET", "activity": [_ACTIVITY_ROW]},
    }
    watch = ["0xBE3F4B43DB5EB49D1F48F53443B9ABCE45DA3B79"]
    r1 = graph.invoke({"payload": payload, "watched_addresses": watch})
    r2 = graph.invoke({"payload": payload, "watched_addresses": watch})
    s1 = r1["emit_summary"]
    s2 = r2["emit_summary"]
    assert s1["processed"] == 1
    assert s2["processed"] == 0
    assert s2["duplicate_count"] == 1


def test_webhook_graph_skips_non_address_activity() -> None:
    graph = compiled_alchemy_webhook_graph(AlchemyWebhookDedupeStore())
    payload = {"type": "OTHER", "webhookId": "w", "id": "e"}
    r = graph.invoke({"payload": payload, "watched_addresses": []})
    summary = r["emit_summary"]
    assert summary["skipped"] is True
    assert summary["processed"] == 0
