"""Map Mercury chain names to Alchemy REST network identifiers."""

from __future__ import annotations

# Chains that Mercury supports for RPC and that Alchemy Prices documents for `network`
# fields on portfolio/prices APIs. Extend as Alchemy adds networks.
_MERCURY_TO_ALCHEMY_NETWORK: dict[str, str] = {
    "ethereum": "eth-mainnet",
    "base": "base-mainnet",
    "arbitrum": "arb-mainnet",
    "optimism": "opt-mainnet",
}


class UnknownAlchemyNetworkError(ValueError):
    """Raised when Mercury has no Alchemy network string for a chain name."""


def mercury_chain_to_alchemy_network(mercury_chain_name: str) -> str:
    """Return the Alchemy `network` enum for a Mercury chain name."""

    key = mercury_chain_name.strip().lower()
    try:
        return _MERCURY_TO_ALCHEMY_NETWORK[key]
    except KeyError as exc:
        supported = ", ".join(sorted(_MERCURY_TO_ALCHEMY_NETWORK))
        msg = (
            f"Alchemy APIs are not configured for chain '{mercury_chain_name}'. "
            f"Supported chains for these APIs: {supported}."
        )
        raise UnknownAlchemyNetworkError(msg) from exc


def alchemy_network_to_mercury_chain(alchemy_network: str) -> str:
    """Map an Alchemy REST `network` string back to a Mercury chain name."""

    key = alchemy_network.strip().lower()
    for mercury, alchemy_net in _MERCURY_TO_ALCHEMY_NETWORK.items():
        if alchemy_net.lower() == key:
            return mercury
    supported_nets = ", ".join(sorted({v for v in _MERCURY_TO_ALCHEMY_NETWORK.values()}))
    msg = (
        f"Unsupported Alchemy network '{alchemy_network}'. "
        f"Mercury maps the following portfolio/price networks: {supported_nets}."
    )
    raise UnknownAlchemyNetworkError(msg)
