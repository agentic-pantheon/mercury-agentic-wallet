"""Alchemy Portfolio API client (`/data/v1/.../assets/tokens/by-address`)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from mercury.alchemy.networks import (
    UnknownAlchemyNetworkError,
    alchemy_network_to_mercury_chain,
    mercury_chain_to_alchemy_network,
)
from mercury.chains import UnsupportedChainError, get_chain_by_name
from mercury.custody.oneclaw import SecretStore
from mercury.models.addresses import InvalidEVMAddressError, normalize_evm_address
from mercury.swaps.base import JsonHttpClient, UrllibJsonHttpClient

ALCHEMY_DATA_BASE_URL = "https://api.g.alchemy.com"

MAX_NETWORKS_PER_ADDRESS = 5


class AlchemyPortfolioValidationError(ValueError):
    """Raised when a portfolio request violates Alchemy limits."""


def _coerce_error(err: Any) -> str | None:
    if err is None:
        return None
    if isinstance(err, str):
        return err
    return str(err)


def _parse_token_balance_int(balance_s: str) -> int | None:
    s = balance_s.strip()
    if not s:
        return None
    lowered = s.lower()
    if lowered.startswith("0x"):
        try:
            return int(s, 16)
        except ValueError:
            return None
    try:
        return int(s, 10)
    except ValueError:
        return None


def _coerce_meta_decimals(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 256 else None
    if isinstance(value, float) and value == int(value):
        iv = int(value)
        return iv if 0 <= iv <= 256 else None
    if isinstance(value, str) and value.strip().isdigit():
        iv = int(value.strip())
        return iv if 0 <= iv <= 256 else None
    return None


def _resolved_decimals_for_display(
    metadata: dict[str, Any] | None,
    token_address: str | None,
) -> int | None:
    if isinstance(metadata, dict):
        dec = _coerce_meta_decimals(metadata.get("decimals"))
        if dec is not None:
            return dec
    if token_address is None:
        return 18
    return None


def _format_units_to_display(raw_int: int, decimals: int) -> str:
    if decimals < 0 or decimals > 256:
        return str(raw_int)
    scale = Decimal(10) ** decimals
    d = Decimal(raw_int) / scale
    if d == 0:
        return "0"
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def compute_portfolio_balance_display(
    balance_s: str,
    *,
    token_address: str | None,
    metadata: dict[str, Any] | None,
) -> str:
    """Format ``tokenBalance`` (hex or decimal str) for humans using metadata decimals."""

    parsed = _parse_token_balance_int(balance_s)
    if parsed is None:
        return balance_s
    dec = _resolved_decimals_for_display(metadata, token_address)
    if dec is None:
        return f"{parsed} (raw base units)"
    return _format_units_to_display(parsed, dec)


def normalize_portfolio_token_rows(
    raw: Any,
    *,
    wallet_address: str,
) -> list[dict[str, Any]]:
    """Normalize Alchemy ``data.tokens`` entries into stable dicts."""

    if not isinstance(raw, list):
        return []

    normalized: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue

        network_raw = row.get("network")
        network_s = network_raw if isinstance(network_raw, str) else ""

        wallet_row = row.get("address")
        wallet_s = wallet_address
        if isinstance(wallet_row, str) and wallet_row.strip():
            try:
                wallet_s = normalize_evm_address(wallet_row)
            except InvalidEVMAddressError:
                wallet_s = wallet_row.strip()

        token_raw = row.get("tokenAddress")
        token_out: str | None
        if token_raw is None:
            token_out = None
        elif isinstance(token_raw, str) and token_raw.strip():
            try:
                token_out = normalize_evm_address(token_raw)
            except InvalidEVMAddressError:
                token_out = token_raw.strip()
        else:
            token_out = None

        balance_raw = row.get("tokenBalance")
        if isinstance(balance_raw, str):
            balance_s = balance_raw
        elif balance_raw is not None:
            balance_s = str(balance_raw)
        else:
            balance_s = ""

        meta_out: dict[str, Any] | None = None
        meta_raw = row.get("tokenMetadata")
        if isinstance(meta_raw, dict):
            decimals = meta_raw.get("decimals")
            logo = meta_raw.get("logo")
            name = meta_raw.get("name")
            symbol = meta_raw.get("symbol")
            meta_out = {
                "decimals": int(decimals) if isinstance(decimals, int) else decimals,
                "logo": logo if isinstance(logo, str) else None,
                "name": name if isinstance(name, str) else None,
                "symbol": symbol if isinstance(symbol, str) else None,
            }

        prices_out: list[dict[str, Any]] = []
        prices_raw = row.get("tokenPrices")
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

        mercury_chain = ""
        if network_s:
            try:
                mercury_chain = alchemy_network_to_mercury_chain(network_s)
            except UnknownAlchemyNetworkError:
                mercury_chain = ""

        normalized.append(
            {
                "wallet_address": wallet_s,
                "network": network_s,
                "mercury_chain": mercury_chain,
                "token_address": token_out,
                "balance": balance_s,
                "balance_display": compute_portfolio_balance_display(
                    balance_s,
                    token_address=token_out,
                    metadata=meta_out,
                ),
                "metadata": meta_out,
                "prices": prices_out,
                "error": _coerce_error(row.get("error")),
            }
        )

    return normalized


def _validate_network_list(mercury_chains: list[str]) -> list[str]:
    if len(mercury_chains) > MAX_NETWORKS_PER_ADDRESS:
        msg = (
            f"Alchemy portfolio allows at most {MAX_NETWORKS_PER_ADDRESS} networks per wallet; "
            f"got {len(mercury_chains)}."
        )
        raise AlchemyPortfolioValidationError(msg)
    if not mercury_chains:
        msg = "Portfolio request requires at least one network."
        raise AlchemyPortfolioValidationError(msg)
    return mercury_chains


@runtime_checkable
class AlchemyPortfolioClientProtocol(Protocol):
    """Protocol for test doubles."""

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
        """Return a dict with ``wallet_address``, ``tokens``, and optional ``page_key``."""


@dataclass(frozen=True)
class AlchemyPortfolioClient:
    """POST Alchemy ``/data/v1/{apiKey}/assets/tokens/by-address`` using 1Claw secrets."""

    secret_store: SecretStore
    api_key_secret_path: str
    http: JsonHttpClient | None = None

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
        """Normalize ``data.tokens`` and optional ``data.pageKey``."""

        if mercury_chains is None:  # pragma: no cover - defensive
            mercury_chains = []
        if len(mercury_chains) > MAX_NETWORKS_PER_ADDRESS:
            msg = (
                f"Alchemy portfolio allows at most {MAX_NETWORKS_PER_ADDRESS} networks per wallet; "
                f"got {len(mercury_chains)}."
            )
            raise AlchemyPortfolioValidationError(msg)

        wallet = normalize_evm_address(wallet_address)

        cleaned_chains: list[str] = []
        network_strings: list[str] = []
        seen: set[str] = set()
        for c in mercury_chains:
            name = c.strip().lower()
            if not name or name in seen:
                continue
            try:
                get_chain_by_name(name)
            except UnsupportedChainError as exc:
                msg = f"Unsupported Mercury chain '{c}' for portfolio lookup."
                raise AlchemyPortfolioValidationError(msg) from exc
            try:
                network_strings.append(mercury_chain_to_alchemy_network(name))
            except UnknownAlchemyNetworkError as exc:
                raise AlchemyPortfolioValidationError(str(exc)) from exc
            cleaned_chains.append(name)
            seen.add(name)

        _validate_network_list(cleaned_chains)

        api_key = self.secret_store.get_secret(self.api_key_secret_path).reveal().strip()
        if not api_key:
            msg = f"Alchemy API key secret at path '{self.api_key_secret_path}' is empty."
            raise AlchemyPortfolioValidationError(msg)

        body: dict[str, Any] = {
            "addresses": [{"address": wallet, "networks": network_strings}],
            "withMetadata": with_metadata,
            "withPrices": with_prices,
            "includeNativeTokens": include_native_tokens,
            "includeErc20Tokens": include_erc20_tokens,
        }
        if page_key is not None and page_key.strip():
            body["pageKey"] = page_key.strip()

        http = self.http or UrllibJsonHttpClient(ALCHEMY_DATA_BASE_URL)
        path = f"data/v1/{api_key}/assets/tokens/by-address"
        response = http.post_json(path, payload=body)

        data = response.get("data")
        page_out: str | None = None
        tokens_raw: list[Any] = []
        if isinstance(data, dict):
            pk = data.get("pageKey")
            if isinstance(pk, str) and pk.strip():
                page_out = pk.strip()
            tr = data.get("tokens")
            if isinstance(tr, list):
                tokens_raw = tr

        tokens = normalize_portfolio_token_rows(tokens_raw, wallet_address=wallet)
        return {
            "wallet_address": wallet,
            "tokens": tokens,
            "page_key": page_out,
        }
