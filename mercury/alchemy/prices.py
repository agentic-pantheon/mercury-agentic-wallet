"""Alchemy Prices API client (`/prices/v1/.../tokens/by-address`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from mercury.alchemy.networks import mercury_chain_to_alchemy_network
from mercury.custody.oneclaw import SecretStore
from mercury.models.addresses import InvalidEVMAddressError, normalize_evm_address
from mercury.swaps.base import JsonHttpClient, UrllibJsonHttpClient

ALCHEMY_PRICES_BASE_URL = "https://api.g.alchemy.com"

MAX_TOKEN_ADDRESS_ENTRIES = 25
MAX_DISTINCT_NETWORKS = 3


class AlchemyTokenPricesValidationError(ValueError):
    """Raised when a prices request violates Alchemy batch or network limits."""


def _validate_batch(entries: list[tuple[str, str]]) -> None:
    if len(entries) > MAX_TOKEN_ADDRESS_ENTRIES:
        msg = (
            f"Alchemy token prices allow at most {MAX_TOKEN_ADDRESS_ENTRIES} "
            f"address entries; got {len(entries)}."
        )
        raise AlchemyTokenPricesValidationError(msg)

    distinct: set[str] = set()
    for chain_name, _addr in entries:
        distinct.add(mercury_chain_to_alchemy_network(chain_name))

    if len(distinct) > MAX_DISTINCT_NETWORKS:
        msg = (
            f"Alchemy token prices allow at most {MAX_DISTINCT_NETWORKS} distinct networks "
            f"per request; got {len(distinct)}."
        )
        raise AlchemyTokenPricesValidationError(msg)


def normalize_token_price_rows(
    raw: Any,
    *,
    mercury_chain_by_network: dict[str, str],
) -> list[dict[str, Any]]:
    """Normalize Alchemy `data` array items into stable dicts."""

    if not isinstance(raw, list):
        return []

    normalized: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        network = row.get("network")
        address = row.get("address")
        prices_raw = row.get("prices")
        err = row.get("error")

        network_s = network if isinstance(network, str) else ""
        address_s = address if isinstance(address, str) else ""
        mercury_chain = mercury_chain_by_network.get(network_s, "")

        prices_out: list[dict[str, Any]] = []
        if isinstance(prices_raw, list):
            for p in prices_raw:
                if not isinstance(p, dict):
                    continue
                currency = p.get("currency")
                value = p.get("value")
                last_updated = p.get("lastUpdatedAt")
                prices_out.append(
                    {
                        "currency": currency if isinstance(currency, str) else str(currency),
                        "value": value,
                        "lastUpdatedAt": last_updated,
                    }
                )

        err_out: str | None
        if err is None:
            err_out = None
        elif isinstance(err, str):
            err_out = err
        else:
            err_out = str(err)

        addr_display = address_s
        try:
            if address_s:
                addr_display = normalize_evm_address(address_s)
        except InvalidEVMAddressError:
            pass

        normalized.append(
            {
                "network": network_s,
                "mercury_chain": mercury_chain,
                "address": addr_display,
                "prices": prices_out,
                "error": err_out,
            }
        )
    return normalized


@runtime_checkable
class AlchemyPricesClientProtocol(Protocol):
    """Protocol for test doubles."""

    def fetch_prices_by_address(self, entries: list[tuple[str, str]]) -> dict[str, Any]:
        """Fetch normalized token prices for (mercury_chain_name, token_address) pairs."""


@dataclass(frozen=True)
class AlchemyTokenPricesClient:
    """POST Alchemy `/prices/v1/{apiKey}/tokens/by-address` with secrets from 1Claw."""

    secret_store: SecretStore
    api_key_secret_path: str
    http: JsonHttpClient | None = None

    def fetch_prices_by_address(self, entries: list[tuple[str, str]]) -> dict[str, Any]:
        """Return ``{"tokens": [...]}`` with per-token ``prices`` and optional ``error``."""

        cleaned: list[tuple[str, str]] = []
        network_for_chain: dict[str, str] = {}
        for chain_name, addr in entries:
            c = chain_name.strip().lower()
            a = normalize_evm_address(addr)
            cleaned.append((c, a))
            network_for_chain[c] = mercury_chain_to_alchemy_network(c)

        _validate_batch(cleaned)

        api_key = self.secret_store.get_secret(self.api_key_secret_path).reveal().strip()
        if not api_key:
            msg = f"Alchemy API key secret at path '{self.api_key_secret_path}' is empty."
            raise AlchemyTokenPricesValidationError(msg)

        mercury_by_network = {net: chain for chain, net in network_for_chain.items()}
        body_addresses = [{"network": network_for_chain[c], "address": a} for c, a in cleaned]

        http = self.http or UrllibJsonHttpClient(ALCHEMY_PRICES_BASE_URL)
        path = f"prices/v1/{api_key}/tokens/by-address"
        response = http.post_json(path, payload={"addresses": body_addresses})
        data = response.get("data")
        tokens = normalize_token_price_rows(data, mercury_chain_by_network=mercury_by_network)
        return {"tokens": tokens}
