import pytest
from mercury.chains import (
    UnsupportedChainError,
    get_chain_by_id,
    get_chain_by_name,
    get_default_chain,
)
from mercury.known_addresses.book import lookup_address


def test_resolves_ethereum_by_name() -> None:
    chain = get_chain_by_name("ethereum")

    assert chain.name == "ethereum"
    assert chain.chain_id == 1
    assert chain.native_symbol == "ETH"
    assert chain.rpc_secret_path == "mercury/rpc/ethereum"


def test_resolves_base_by_name() -> None:
    chain = get_chain_by_name("base")

    assert chain.name == "base"
    assert chain.chain_id == 8453
    assert chain.native_symbol == "ETH"
    assert chain.rpc_secret_path == "mercury/rpc/base"


def test_resolves_ethereum_by_chain_id() -> None:
    assert get_chain_by_id(1).name == "ethereum"


def test_resolves_base_by_chain_id() -> None:
    assert get_chain_by_id(8453).name == "base"


def test_resolves_monad_by_chain_id() -> None:
    chain = get_chain_by_id(143)

    assert chain.name == "monad"
    assert chain.native_symbol == "MON"
    assert chain.rpc_secret_path == "mercury/rpc/monad"


def test_unsupported_chain_raises_clear_error() -> None:
    with pytest.raises(UnsupportedChainError, match="Unsupported chain name 'polygon'"):
        get_chain_by_name("polygon")

    with pytest.raises(UnsupportedChainError, match="Unsupported chain ID '137'"):
        get_chain_by_id(137)


def test_default_chain_is_ethereum() -> None:
    assert get_default_chain().name == "ethereum"


@pytest.mark.parametrize(
    "chain_name",
    ("ethereum", "base", "arbitrum", "optimism"),
)
def test_wrapped_native_matches_known_weth(chain_name: str) -> None:
    chain = get_chain_by_name(chain_name)
    assert chain.wrapped_native_token_address == lookup_address(chain_name, "token", "WETH")


def test_monad_wrapped_native_unknown() -> None:
    assert get_chain_by_name("monad").wrapped_native_token_address is None
