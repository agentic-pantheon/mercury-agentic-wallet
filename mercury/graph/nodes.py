"""LangGraph nodes for read-only Mercury execution."""

from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage

from mercury.alchemy.networks import UnknownAlchemyNetworkError, mercury_chain_to_alchemy_network
from mercury.chains import UnsupportedChainError, get_chain_by_name, get_default_chain
from mercury.graph.intents import ReadOnlyIntentKind, UnsupportedIntentError, parse_readonly_intent
from mercury.graph.responses import (
    format_error_response,
    format_success_response,
    format_unsupported_response,
)
from mercury.graph.state import MercuryState
from mercury.models.errors import MercuryErrorInfo, normalize_exception
from mercury.tools.registry import ReadOnlyToolRegistry


def parse_intent(state: MercuryState) -> MercuryState:
    """Parse structured read-only input into graph state."""

    try:
        parsed_intent = parse_readonly_intent(state.get("raw_input"), state.get("messages"))
    except UnsupportedIntentError as exc:
        return {"error": normalize_exception(exc, stage="parse_intent")}

    return {
        "parsed_intent": parsed_intent.model_dump(mode="json"),
        "read_result": {"intent_kind": parsed_intent.kind.value},
    }


def resolve_chain(state: MercuryState) -> MercuryState:
    """Resolve the requested chain, defaulting to Ethereum."""

    parsed_intent = state.get("parsed_intent", {})
    if parsed_intent.get("kind") == ReadOnlyIntentKind.TOKEN_PRICES.value:
        return _resolve_chain_for_token_prices(parsed_intent)
    if parsed_intent.get("kind") == ReadOnlyIntentKind.PORTFOLIO_TOKENS.value:
        return _resolve_chain_for_portfolio_tokens(parsed_intent)

    chain_name = parsed_intent.get("chain")
    if not isinstance(chain_name, str) or not chain_name:
        chain_name = get_default_chain().name

    try:
        chain_config = get_chain_by_name(chain_name)
    except UnsupportedChainError as exc:
        return {
            "error": normalize_exception(exc, stage="resolve_chain"),
            "chain_name": chain_name,
        }

    return {
        "chain_name": chain_config.name,
        "chain_config": chain_config,
        "chain_reference": chain_config.to_reference(),
    }


def _resolve_chain_for_token_prices(parsed_intent: dict[str, Any]) -> MercuryState:
    """Validate Mercury chains and Alchemy price API coverage for each entry."""

    tokens = parsed_intent.get("tokens") or []
    if not tokens or not isinstance(tokens, list):
        exc = ValueError("token_prices intent is missing tokens.")
        return {"error": normalize_exception(exc, stage="resolve_chain")}

    chain_names: list[str] = []
    for entry in tokens:
        if not isinstance(entry, dict):
            continue
        c = entry.get("chain")
        if isinstance(c, str) and c.strip():
            chain_names.append(c.strip().lower())

    if not chain_names:
        exc = ValueError("token_prices tokens must include a chain per entry.")
        return {"error": normalize_exception(exc, stage="resolve_chain")}

    distinct = sorted(set(chain_names))
    for name in distinct:
        try:
            get_chain_by_name(name)
        except UnsupportedChainError as exc:
            return {
                "error": normalize_exception(exc, stage="resolve_chain"),
                "chain_name": name,
            }
        try:
            mercury_chain_to_alchemy_network(name)
        except UnknownAlchemyNetworkError as exc:
            return {"error": normalize_exception(exc, stage="resolve_chain"), "chain_name": name}

    first = chain_names[0]
    chain_config = get_chain_by_name(first)
    return {
        "chain_name": chain_config.name,
        "chain_config": chain_config,
        "chain_reference": chain_config.to_reference(),
    }


def _resolve_chain_for_portfolio_tokens(parsed_intent: dict[str, Any]) -> MercuryState:
    """Validate Mercury chains and Alchemy portfolio API coverage."""

    chains = parsed_intent.get("chains") or []
    if not chains or not isinstance(chains, list):
        exc = ValueError("portfolio_tokens intent is missing chains.")
        return {"error": normalize_exception(exc, stage="resolve_chain")}

    names: list[str] = []
    for item in chains:
        if isinstance(item, str) and item.strip():
            names.append(item.strip().lower())

    if not names:
        exc = ValueError("portfolio_tokens chains must include at least one chain name.")
        return {"error": normalize_exception(exc, stage="resolve_chain")}

    distinct = sorted(set(names))
    for name in distinct:
        try:
            get_chain_by_name(name)
        except UnsupportedChainError as exc:
            return {
                "error": normalize_exception(exc, stage="resolve_chain"),
                "chain_name": name,
            }
        try:
            mercury_chain_to_alchemy_network(name)
        except UnknownAlchemyNetworkError as exc:
            return {"error": normalize_exception(exc, stage="resolve_chain"), "chain_name": name}

    first = names[0]
    chain_config = get_chain_by_name(first)
    return {
        "chain_name": chain_config.name,
        "chain_config": chain_config,
        "chain_reference": chain_config.to_reference(),
    }


def make_read_tool_node(
    tool_name: str,
    registry: ReadOnlyToolRegistry,
) -> Callable[[MercuryState], MercuryState]:
    """Create a graph node that executes one read-only tool."""

    def execute_read_tool(state: MercuryState) -> MercuryState:
        tool_input = _tool_input_for_state(state)
        try:
            tool_result = registry.execute(tool_name, tool_input)
        except Exception as exc:
            return {
                "selected_tool_name": tool_name,
                "tool_input": tool_input,
                "error": normalize_exception(exc, stage=f"read_tool:{tool_name}"),
            }

        return {
            "selected_tool_name": tool_name,
            "tool_input": tool_input,
            "tool_result": tool_result,
            "read_result": tool_result,
        }

    return execute_read_tool


def format_response(state: MercuryState) -> MercuryState:
    """Format successful or failed read-only execution."""

    if state.get("error"):
        response_text = format_error_response(state["error"])
    else:
        parsed_intent = state.get("parsed_intent", {})
        intent_kind = parsed_intent.get("kind", "")
        response_text = format_success_response(
            intent_kind=str(intent_kind),
            tool_result=state.get("tool_result", {}),
        )

    return {"response_text": response_text, "messages": [AIMessage(content=response_text)]}


def unsupported_response(state: MercuryState) -> MercuryState:
    """Return a clear response for unsupported or invalid intents."""

    parsed_intent = state.get("parsed_intent", {})
    error_info = state.get("error")
    if error_info is not None:
        reason: str | MercuryErrorInfo = error_info
    else:
        raw_reason = parsed_intent.get("reason")
        reason = raw_reason if isinstance(raw_reason, str) else "Unsupported wallet intent."

    response_text = format_unsupported_response(reason)
    return {"response_text": response_text, "messages": [AIMessage(content=response_text)]}


def respond(state: MercuryState) -> MercuryState:
    """Compatibility alias for older smoke tests."""

    return format_response(state)


def _tool_input_for_state(state: MercuryState) -> dict[str, Any]:
    parsed_intent = state.get("parsed_intent", {})
    chain_name = state.get("chain_name", get_default_chain().name)
    intent_kind = parsed_intent.get("kind")
    base = {"chain": chain_name}

    if intent_kind == ReadOnlyIntentKind.NATIVE_BALANCE.value:
        return {**base, "wallet_address": parsed_intent["wallet_address"]}
    if intent_kind == ReadOnlyIntentKind.ERC20_BALANCE.value:
        return {
            **base,
            "token_address": parsed_intent["token_address"],
            "wallet_address": parsed_intent["wallet_address"],
        }
    if intent_kind == ReadOnlyIntentKind.ERC20_ALLOWANCE.value:
        return {
            **base,
            "token_address": parsed_intent["token_address"],
            "owner_address": parsed_intent["owner_address"],
            "spender_address": parsed_intent["spender_address"],
        }
    if intent_kind == ReadOnlyIntentKind.ERC20_METADATA.value:
        return {**base, "token_address": parsed_intent["token_address"]}
    if intent_kind == ReadOnlyIntentKind.CONTRACT_READ.value:
        return {
            **base,
            "contract_address": parsed_intent["contract_address"],
            "abi_fragment": parsed_intent["abi_fragment"],
            "function_name": parsed_intent["function_name"],
            "args": parsed_intent.get("args", []),
        }
    if intent_kind == ReadOnlyIntentKind.KNOWN_ADDRESS.value:
        return {
            **base,
            "category": parsed_intent["category"],
            "key": parsed_intent["key"],
        }
    if intent_kind == ReadOnlyIntentKind.TOKEN_PRICES.value:
        tokens = parsed_intent.get("tokens") or []
        return {"tokens": tokens}
    if intent_kind == ReadOnlyIntentKind.PORTFOLIO_TOKENS.value:
        chains = parsed_intent.get("chains") or []
        page_key = parsed_intent.get("page_key")
        payload: dict[str, Any] = {
            "wallet_address": parsed_intent["wallet_address"],
            "chains": chains,
            "with_metadata": bool(parsed_intent.get("with_metadata", True)),
            "with_prices": bool(parsed_intent.get("with_prices", True)),
            "include_native_tokens": bool(parsed_intent.get("include_native_tokens", True)),
            "include_erc20_tokens": bool(parsed_intent.get("include_erc20_tokens", True)),
        }
        if isinstance(page_key, str) and page_key.strip():
            payload["page_key"] = page_key.strip()
        return payload

    msg = f"Unsupported read-only intent kind: {intent_kind}."
    raise ValueError(msg)
