"""Static chain registry with 1Claw RPC secret metadata."""

from __future__ import annotations

from mercury.config import MercurySettings, get_settings
from mercury.models.chain import ChainConfig

DEFAULT_CHAIN_NAME = "ethereum"


class UnsupportedChainError(ValueError):
    """Raised when a chain is not supported by Mercury."""


def _chain_configs(settings: MercurySettings | None = None) -> tuple[ChainConfig, ...]:
    """Build chain rows from the active settings (defaults or explicit override)."""

    s = settings if settings is not None else get_settings()
    return (
        ChainConfig(
            name="ethereum",
            chain_id=1,
            native_symbol="ETH",
            rpc_secret_path=s.ethereum_rpc_secret_path,
            block_explorer_url="https://etherscan.io",
        ),
        ChainConfig(
            name="base",
            chain_id=8453,
            native_symbol="ETH",
            rpc_secret_path=s.base_rpc_secret_path,
            block_explorer_url="https://basescan.org",
        ),
        ChainConfig(
            name="arbitrum",
            chain_id=42161,
            native_symbol="ETH",
            rpc_secret_path=s.arbitrum_rpc_secret_path,
            block_explorer_url="https://arbiscan.org",
        ),
        ChainConfig(
            name="optimism",
            chain_id=10,
            native_symbol="ETH",
            rpc_secret_path=s.optimism_rpc_secret_path,
            block_explorer_url="https://optimistic.etherscan.io",
        ),
        ChainConfig(
            name="monad",
            chain_id=143,
            native_symbol="MON",
            rpc_secret_path=s.monad_rpc_secret_path,
            block_explorer_url="https://monadvision.com",
        ),
    )


def _supported_chains_text() -> str:
    return ", ".join(sorted(c.name for c in _chain_configs()))


def get_chain_by_name(name: str) -> ChainConfig:
    """Return a supported chain by canonical name."""

    normalized_name = name.strip().lower()
    chains = {c.name: c for c in _chain_configs()}
    try:
        return chains[normalized_name]
    except KeyError as exc:
        msg = f"Unsupported chain name '{name}'. Supported chains: {_supported_chains_text()}."
        raise UnsupportedChainError(msg) from exc


def get_chain_by_id(chain_id: int) -> ChainConfig:
    """Return a supported chain by EVM chain ID."""

    for chain in _chain_configs():
        if chain.chain_id == chain_id:
            return chain
    msg = f"Unsupported chain ID '{chain_id}'. Supported chains: {_supported_chains_text()}."
    raise UnsupportedChainError(msg)


def get_default_chain() -> ChainConfig:
    """Return Mercury's default chain."""

    return get_chain_by_name(DEFAULT_CHAIN_NAME)


def list_chains() -> tuple[ChainConfig, ...]:
    """Return all supported chains."""

    return tuple(_chain_configs())


def __getattr__(name: str) -> ChainConfig:
    """Lazily expose ``ETHEREUM``, ``BASE``, … with paths from current settings."""

    lazy = {
        "ETHEREUM": "ethereum",
        "BASE": "base",
        "ARBITRUM": "arbitrum",
        "OPTIMISM": "optimism",
        "MONAD": "monad",
    }
    if name in lazy:
        return get_chain_by_name(lazy[name])
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
