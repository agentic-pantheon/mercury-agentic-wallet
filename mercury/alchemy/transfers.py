"""Alchemy JSON-RPC Transfers API client (`alchemy_getAssetTransfers`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from mercury.alchemy.networks import (
    UnknownAlchemyNetworkError,
    alchemy_network_to_mercury_chain,
    mercury_chain_to_alchemy_network,
)
from mercury.chains import UnsupportedChainError, get_chain_by_name
from mercury.custody.oneclaw import SecretStore
from mercury.models.addresses import InvalidEVMAddressError, normalize_evm_address
from mercury.swaps.base import JsonHttpClient, UrllibJsonHttpClient

TRANSFER_DEFAULT_CATEGORIES: tuple[str, ...] = (
    "external",
    "internal",
    "erc20",
    "erc721",
    "erc1155",
)

ALLOWED_TRANSFER_CATEGORIES: frozenset[str] = frozenset(
    {
        *TRANSFER_DEFAULT_CATEGORIES,
        "specialnft",
    }
)


class AlchemyTransfersValidationError(ValueError):
    """Raised when transfer history parameters are invalid."""


def normalize_block_param(block: str | int | None) -> str | None:
    """Convert optional block height to Alchemy hex-style block references."""

    if block is None:
        return None
    if isinstance(block, int):
        if block < 0:
            msg = "from_block/to_block must be non-negative when passed as integers."
            raise AlchemyTransfersValidationError(msg)
        return hex(block)
    segment = str(block).strip()
    if not segment:
        return None
    lowered = segment.lower()
    if lowered == "latest":
        return "latest"
    if lowered.startswith("0x"):
        return segment
    if segment.isdecimal():
        return hex(int(segment, 10))
    msg = f"Invalid block reference {segment!r}; use int, hex string, decimal string, or 'latest'."
    raise AlchemyTransfersValidationError(msg)


def build_asset_transfer_params(
    *,
    wallet_address: str,
    leg: Literal["incoming", "outgoing"],
    categories: list[str],
    from_block: str | int | None = None,
    to_block: str | int | None = None,
    max_count: int,
    page_key: str | None,
    with_metadata: bool,
    exclude_zero_value: bool,
) -> dict[str, Any]:
    """Build params for one ``alchemy_getAssetTransfers`` call."""

    wallet = normalize_evm_address(wallet_address)
    if leg == "incoming":
        params: dict[str, Any] = {"toAddress": wallet}
    else:
        params = {"fromAddress": wallet}

    fb = normalize_block_param(from_block)
    if fb is not None:
        params["fromBlock"] = fb
    tb = normalize_block_param(to_block)
    if tb is not None:
        params["toBlock"] = tb

    params["category"] = list(categories)
    params["order"] = "desc"
    params["maxCount"] = hex(max_count)
    params["withMetadata"] = bool(with_metadata)
    params["excludeZeroValue"] = bool(exclude_zero_value)

    if page_key is not None and page_key.strip():
        params["pageKey"] = page_key.strip()

    return params


def _normalize_optional_address(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return normalize_evm_address(value)
    except InvalidEVMAddressError:
        return value.strip()


def normalize_asset_transfer_rows(raw: Any, *, network: str) -> list[dict[str, Any]]:
    """Normalize Alchemy ``transfers`` entries into stable dicts."""

    if not isinstance(raw, list):
        return []

    mercury_chain = ""
    if network:
        try:
            mercury_chain = alchemy_network_to_mercury_chain(network)
        except UnknownAlchemyNetworkError:
            mercury_chain = ""

    normalized: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue

        block_raw = row.get("blockNum")
        block_num: int | str | None
        if isinstance(block_raw, str) and block_raw.startswith("0x"):
            try:
                block_num = int(block_raw, 16)
            except ValueError:
                block_num = block_raw
        elif isinstance(block_raw, int):
            block_num = block_raw
        elif block_raw is None:
            block_num = None
        else:
            block_num = str(block_raw)

        raw_contract = row.get("rawContract")
        if raw_contract is not None and not isinstance(raw_contract, dict):
            raw_contract = {"value": raw_contract}

        metadata = row.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            metadata = {"raw": metadata}

        value = row.get("value")

        normalized.append(
            {
                "hash": row.get("hash") if isinstance(row.get("hash"), str) else "",
                "block_num": block_num,
                "from_address": _normalize_optional_address(row.get("from")),
                "to_address": _normalize_optional_address(row.get("to")),
                "category": row.get("category") if isinstance(row.get("category"), str) else "",
                "asset": row.get("asset"),
                "value": value,
                "raw_contract": raw_contract,
                "metadata": metadata,
                "network": network,
                "mercury_chain": mercury_chain,
            }
        )

    return normalized


def _merge_and_sort_transfers(
    rows: list[dict[str, Any]],
    *,
    max_count: int,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for r in rows:
        h = r.get("hash")
        key = str(h) if h else ""
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(r)

    def sort_key(item: dict[str, Any]) -> tuple[int, str]:
        bn = item.get("block_num")
        bi = bn if isinstance(bn, int) else 0
        return (-bi, str(item.get("hash", "")))

    unique.sort(key=sort_key)
    return unique[:max_count]


def _rpc_request_body(params_obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "alchemy_getAssetTransfers",
        "params": [params_obj],
    }


@runtime_checkable
class AlchemyTransfersClientProtocol(Protocol):
    """Protocol for test doubles."""

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
        """Return wallet/network metadata, normalized ``transfers``, and optional ``page_key``."""


@dataclass(frozen=True)
class AlchemyTransfersClient:
    """POST ``alchemy_getAssetTransfers`` to ``https://{network}.g.alchemy.com/v2/{apiKey}``."""

    secret_store: SecretStore
    api_key_secret_path: str
    http: JsonHttpClient | None = None

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
        name = mercury_chain.strip().lower()
        try:
            get_chain_by_name(name)
        except UnsupportedChainError as exc:
            msg = f"Unsupported Mercury chain '{mercury_chain}' for transfer history."
            raise AlchemyTransfersValidationError(msg) from exc

        try:
            network = mercury_chain_to_alchemy_network(name)
        except UnknownAlchemyNetworkError as exc:
            raise AlchemyTransfersValidationError(str(exc)) from exc

        wallet = normalize_evm_address(wallet_address)
        dir_norm = direction.strip().lower()

        api_key = self.secret_store.get_secret(self.api_key_secret_path).reveal().strip()
        if not api_key:
            msg = f"Alchemy API key secret at path '{self.api_key_secret_path}' is empty."
            raise AlchemyTransfersValidationError(msg)

        base_rpc = f"https://{network}.g.alchemy.com/v2"
        http = self.http or UrllibJsonHttpClient(base_rpc)

        if dir_norm == "both":
            outgoing_params = build_asset_transfer_params(
                wallet_address=wallet,
                leg="outgoing",
                categories=categories,
                from_block=from_block,
                to_block=to_block,
                max_count=max_count,
                page_key=None,
                with_metadata=with_metadata,
                exclude_zero_value=exclude_zero_value,
            )
            incoming_params = build_asset_transfer_params(
                wallet_address=wallet,
                leg="incoming",
                categories=categories,
                from_block=from_block,
                to_block=to_block,
                max_count=max_count,
                page_key=None,
                with_metadata=with_metadata,
                exclude_zero_value=exclude_zero_value,
            )
            raw_a = http.post_json(api_key, payload=_rpc_request_body(outgoing_params))
            raw_b = http.post_json(api_key, payload=_rpc_request_body(incoming_params))
            transfers_a, _ = self._parse_rpc_result(raw_a)
            transfers_b, _ = self._parse_rpc_result(raw_b)
            merged = (
                normalize_asset_transfer_rows(transfers_a, network=network)
                + normalize_asset_transfer_rows(transfers_b, network=network)
            )
            ordered = _merge_and_sort_transfers(merged, max_count=max_count)
            return {
                "wallet_address": wallet,
                "mercury_chain": name,
                "network": network,
                "transfers": ordered,
                "page_key": None,
            }

        if dir_norm not in {"incoming", "outgoing"}:
            msg = f"direction must be incoming, outgoing, or both; got {direction!r}."
            raise AlchemyTransfersValidationError(msg)

        leg: Literal["incoming", "outgoing"] = "incoming" if dir_norm == "incoming" else "outgoing"
        params = build_asset_transfer_params(
            wallet_address=wallet,
            leg=leg,
            categories=categories,
            from_block=from_block,
            to_block=to_block,
            max_count=max_count,
            page_key=page_key,
            with_metadata=with_metadata,
            exclude_zero_value=exclude_zero_value,
        )
        raw = http.post_json(api_key, payload=_rpc_request_body(params))
        transfers_raw, page_out = self._parse_rpc_result(raw)
        rows = normalize_asset_transfer_rows(transfers_raw, network=network)

        return {
            "wallet_address": wallet,
            "mercury_chain": name,
            "network": network,
            "transfers": rows,
            "page_key": page_out,
        }

    def _parse_rpc_result(self, raw: dict[str, Any]) -> tuple[list[Any], str | None]:
        err = raw.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            text = str(msg) if msg is not None else str(err)
            raise AlchemyTransfersValidationError(text)
        result = raw.get("result")
        if not isinstance(result, dict):
            msg = "Alchemy transfer history response missing result object."
            raise AlchemyTransfersValidationError(msg)

        transfers = result.get("transfers")
        if not isinstance(transfers, list):  # pragma: no cover - defensive
            transfers = []

        page_out: str | None = None
        pk = result.get("pageKey")
        if isinstance(pk, str) and pk.strip():
            page_out = pk.strip()

        return transfers, page_out
