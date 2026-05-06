from mercury.graph.intents import parse_readonly_intent
from mercury.graph.nodes import resolve_chain
from mercury.graph.router import (
    ROUTE_CONTRACT_READ,
    ROUTE_ERC20_ALLOWANCE,
    ROUTE_ERC20_BALANCE,
    ROUTE_ERC20_METADATA,
    ROUTE_NATIVE_BALANCE,
    ROUTE_PORTFOLIO_TOKENS,
    ROUTE_RESOLVE_CHAIN,
    ROUTE_TOKEN_PRICES,
    ROUTE_TRANSFER_HISTORY,
    ROUTE_UNSUPPORTED,
    route_after_parse,
    route_read_tool,
)
from mercury.graph.state import MercuryState

WALLET = "0x000000000000000000000000000000000000dEaD"
TOKEN = "0x000000000000000000000000000000000000cafE"
OWNER = "0x0000000000000000000000000000000000000001"
SPENDER = "0x0000000000000000000000000000000000000002"
CONTRACT = "0x000000000000000000000000000000000000bEEF"


def test_portfolio_tokens_intent_routes_to_portfolio_node() -> None:
    state = _state_for(
        {
            "kind": "portfolio_tokens",
            "wallet_address": WALLET,
            "chain": "ethereum",
        }
    )

    assert route_after_parse(state) == ROUTE_RESOLVE_CHAIN
    assert route_read_tool(state) == ROUTE_PORTFOLIO_TOKENS


def test_transfer_history_intent_routes_to_history_node() -> None:
    state = _state_for(
        {
            "kind": "transfer_history",
            "wallet_address": WALLET,
            "chain": "ethereum",
        }
    )

    assert route_after_parse(state) == ROUTE_RESOLVE_CHAIN
    assert route_read_tool(state) == ROUTE_TRANSFER_HISTORY


def test_token_prices_intent_routes_to_token_prices_node() -> None:
    state = _state_for(
        {
            "kind": "token_prices",
            "tokens": [{"chain": "base", "token_address": TOKEN}],
        }
    )

    assert route_after_parse(state) == ROUTE_RESOLVE_CHAIN
    assert route_read_tool(state) == ROUTE_TOKEN_PRICES


def test_native_balance_intent_routes_to_native_balance_node() -> None:
    state = _state_for({"kind": "native_balance", "wallet_address": WALLET})

    assert route_after_parse(state) == ROUTE_RESOLVE_CHAIN
    assert route_read_tool(state) == ROUTE_NATIVE_BALANCE


def test_erc20_balance_intent_routes_to_erc20_balance_node() -> None:
    state = _state_for({"kind": "erc20_balance", "token_address": TOKEN, "wallet_address": WALLET})

    assert route_read_tool(state) == ROUTE_ERC20_BALANCE


def test_allowance_intent_routes_to_allowance_node() -> None:
    state = _state_for(
        {
            "kind": "erc20_allowance",
            "token_address": TOKEN,
            "owner_address": OWNER,
            "spender_address": SPENDER,
        }
    )

    assert route_read_tool(state) == ROUTE_ERC20_ALLOWANCE


def test_metadata_intent_routes_to_metadata_node() -> None:
    state = _state_for({"kind": "erc20_metadata", "token_address": TOKEN})

    assert route_read_tool(state) == ROUTE_ERC20_METADATA


def test_contract_read_intent_routes_to_contract_read_node() -> None:
    state = _state_for(
        {
            "kind": "contract_read",
            "contract_address": CONTRACT,
            "abi_fragment": [{"type": "function", "name": "totalSupply"}],
            "function_name": "totalSupply",
        }
    )

    assert route_read_tool(state) == ROUTE_CONTRACT_READ


def test_unsupported_intent_routes_to_unsupported_response() -> None:
    state = _state_for({"kind": "swap", "wallet_address": WALLET})

    assert route_after_parse(state) == ROUTE_UNSUPPORTED


def test_missing_chain_defaults_to_ethereum() -> None:
    state = _state_for({"kind": "native_balance", "wallet_address": WALLET})

    update = resolve_chain(state)

    assert update["chain_reference"].name == "ethereum"
    assert update["chain_name"] == "ethereum"


def test_base_resolves_when_specified() -> None:
    state = _state_for({"kind": "native_balance", "wallet_address": WALLET, "chain": "base"})

    update = resolve_chain(state)

    assert update["chain_reference"].name == "base"
    assert update["chain_name"] == "base"


def test_unsupported_chain_is_rejected() -> None:
    state = _state_for({"kind": "native_balance", "wallet_address": WALLET, "chain": "polygon"})

    update = resolve_chain(state)

    assert "Unsupported chain name" in update["error"].message
    assert "https://" not in update["error"].message


def _state_for(raw_input: dict[str, object]) -> MercuryState:
    parsed = parse_readonly_intent(raw_input)
    return {"raw_input": raw_input, "parsed_intent": parsed.model_dump(mode="json")}
