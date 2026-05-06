"""Parse and normalize Alchemy ``ADDRESS_ACTIVITY`` webhook payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ADDRESS_ACTIVITY = "ADDRESS_ACTIVITY"


def normalize_evm_address(addr: str | None) -> str | None:
    """Lowercase EVM-style addresses for stable comparisons (MVP; accepts any ``0x`` hex)."""

    if addr is None or not isinstance(addr, str):
        return None
    s = addr.strip()
    if not s:
        return None
    return s.lower()


def _parse_address_list(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return []
    parts = raw.split(",")
    out: list[str] = []
    for part in parts:
        norm = normalize_evm_address(part)
        if norm:
            out.append(norm)
    return out


def merge_watched_addresses(
    query_csv: str | None,
    payload: Mapping[str, Any] | None,
) -> list[str]:
    """Merge optional watched addresses from query string and ``payload['metadata']``.

    ``metadata`` may include ``watched_addresses`` as a comma-separated string or a
    JSON array of strings (documented extension; Alchemy does not add this field).

    When the merged list is **empty**, callers should treat **all** normalized activity
    rows as relevant (no ``toAddress`` filter). When non-empty, keep rows whose
    ``to_address`` is in the watch set (after case normalization).
    """

    seen: set[str] = set()
    order: list[str] = []
    for addr in _parse_address_list(query_csv):
        if addr not in seen:
            seen.add(addr)
            order.append(addr)
    if payload is not None:
        meta = payload.get("metadata")
        if isinstance(meta, dict):
            wa = meta.get("watched_addresses")
            if isinstance(wa, str):
                candidates = _parse_address_list(wa)
            elif isinstance(wa, list):
                candidates = []
                for item in wa:
                    if isinstance(item, str):
                        n = normalize_evm_address(item)
                        if n:
                            candidates.append(n)
            else:
                candidates = []
            for addr in candidates:
                if addr not in seen:
                    seen.add(addr)
                    order.append(addr)
    return order


def _log_index_for_dedupe(row: Mapping[str, Any]) -> str:
    log = row.get("log")
    if isinstance(log, dict):
        li = log.get("logIndex")
        if li is not None:
            return str(li).lower()
    trace = row.get("typeTraceAddress")
    if trace is not None:
        return str(trace)
    return "na"


def row_to_alert(*, network: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """Map one ``event.activity[]`` row to a stable alert dict."""

    tx_hash = row.get("hash")
    if not isinstance(tx_hash, str) or not tx_hash:
        log = row.get("log")
        if isinstance(log, dict):
            th = log.get("transactionHash")
            if isinstance(th, str):
                tx_hash = th
    if not isinstance(tx_hash, str):
        tx_hash = ""

    raw_contract = row.get("rawContract")
    contract_address: str | None = None
    if isinstance(raw_contract, dict):
        addr = raw_contract.get("address")
        if isinstance(addr, str):
            contract_address = normalize_evm_address(addr)
    log = row.get("log")
    if contract_address is None and isinstance(log, dict):
        la = log.get("address")
        if isinstance(la, str):
            contract_address = normalize_evm_address(la)

    removed = False
    if isinstance(log, dict) and log.get("removed") is True:
        removed = True

    value = row.get("value")
    # Preserve numeric JSON types; leave special cases as-is
    asset = row.get("asset")
    category = row.get("category")

    return {
        "network": network,
        "tx_hash": tx_hash.lower() if isinstance(tx_hash, str) else str(tx_hash),
        "from_address": normalize_evm_address(row.get("fromAddress")),
        "to_address": normalize_evm_address(row.get("toAddress")),
        "category": str(category) if category is not None else None,
        "asset": asset if isinstance(asset, str) else None,
        "value": value,
        "contract_address": contract_address,
        "removed": removed,
        "log_index": _log_index_for_dedupe(row),
    }


def normalize_activity_rows(*, network: str, activity: list[Any] | None) -> list[dict[str, Any]]:
    """Normalize Alchemy ``event.activity`` to alert dicts (skips non-mapping rows)."""

    if not activity:
        return []
    alerts: list[dict[str, Any]] = []
    for row in activity:
        if isinstance(row, Mapping):
            alerts.append(row_to_alert(network=network, row=row))
    return alerts


def filter_incoming_for_watched(
    alerts: list[dict[str, Any]],
    watched_lower: list[str],
) -> list[dict[str, Any]]:
    """Keep alerts whose ``to_address`` is watched; if ``watched_lower`` is empty, keep all."""

    if not watched_lower:
        return list(alerts)
    watch: set[str] = set()
    for raw in watched_lower:
        norm = normalize_evm_address(raw)
        if norm:
            watch.add(norm)
    if not watch:
        return list(alerts)
    return [a for a in alerts if a.get("to_address") in watch]