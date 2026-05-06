"""Resolve EVM addresses from hex strings or ENS names via mainnet ENS."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, cast, runtime_checkable

from ens import exceptions as ens_exceptions  # type: ignore[import-untyped]
from web3 import Web3

from mercury.chains.ens_coin_type import evm_chain_id_to_ens_coin_type
from mercury.chains.registry import (
    UnsupportedChainError,
    get_chain_by_name,
    get_default_chain,
)
from mercury.models.addresses import InvalidEVMAddressError, normalize_evm_address
from mercury.providers.web3 import Web3ProviderFactory


def looks_like_potential_ens_name(raw: str) -> bool:
    """Heuristic: dot-separated label(s), not a hex address.

    Per ENS guidance, many TLDs are valid; do not special-case only ``.eth``.
    """

    candidate = raw.strip()
    if not candidate or "." not in candidate:
        return False
    if Web3.is_address(candidate):
        return False
    if candidate.lower().startswith("0x"):
        return False
    labels = candidate.split(".")
    return all(bool(label.strip()) for label in labels)


class EnsResolutionError(ValueError):
    """ENS forward resolution failed for a human-readable name."""


@runtime_checkable
class EVMIdentifierResolver(Protocol):
    """Resolve a user-provided EVM target (hex or ENS) for a given operation chain."""

    def resolve_evm_identifier(self, raw: str, chain_name: str) -> str:
        """Return a checksummed ``0x`` address."""


@dataclass
class Web3EnsAddressResolver:
    """Resolve via Ethereum mainnet ``Web3.ens`` with ENSIP-11 coin types."""

    provider_factory: Web3ProviderFactory
    _cache: dict[tuple[str, int, str], str] = field(default_factory=dict)

    def resolve_evm_identifier(self, raw: str, chain_name: str) -> str:
        candidate = raw.strip()
        if not candidate:
            raise EnsResolutionError("Address or ENS name must not be empty.")

        if Web3.is_address(candidate):
            return normalize_evm_address(candidate)

        if not looks_like_potential_ens_name(candidate):
            raise InvalidEVMAddressError("Invalid EVM address.")

        try:
            chain = get_chain_by_name(chain_name)
        except UnsupportedChainError as exc:
            raise EnsResolutionError(str(exc)) from exc
        coin_type = evm_chain_id_to_ens_coin_type(chain.chain_id)
        cache_key = (candidate.lower(), coin_type, "forward")
        if cache_key in self._cache:
            return self._cache[cache_key]

        w3 = self.provider_factory.create("ethereum").client
        ens = cast(Any, w3.ens)
        try:
            resolved = ens.address(candidate, coin_type=coin_type)
        except ens_exceptions.InvalidName as exc:
            raise EnsResolutionError(f"Invalid ENS name: {candidate!r}.") from exc
        except ens_exceptions.ResolverNotFound as exc:
            raise EnsResolutionError(f"No ENS resolver for name: {candidate!r}.") from exc
        except ens_exceptions.UnsupportedFunction as exc:
            raise EnsResolutionError(
                f"ENS resolver cannot resolve an address for: {candidate!r}."
            ) from exc
        except ens_exceptions.ENSException as exc:
            raise EnsResolutionError(f"ENS resolution failed for {candidate!r}.") from exc

        if resolved is None:
            raise EnsResolutionError(
                f"No address record found for ENS name {candidate!r} on this chain."
            )

        out = normalize_evm_address(str(resolved))
        self._cache[cache_key] = out
        return out


def effective_chain_name_for_resolution(merged: dict[str, object]) -> str:
    """Chain used for ENSIP-11 coin type selection (intent ``chain`` or default)."""

    raw = merged.get("chain")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return get_default_chain().name
