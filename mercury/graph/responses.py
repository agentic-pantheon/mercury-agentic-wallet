"""User-facing response formatting for read-only graph execution."""

from __future__ import annotations

from typing import Any

from mercury.graph.intents import ReadOnlyIntentKind
from mercury.models.errors import MercuryErrorInfo


def sanitize_error(error: BaseException | str) -> str:
    """Return a user-safe error string (legacy helper; uses service redaction)."""

    from mercury.service.logging import redact_error_message

    return redact_error_message(error)


def format_success_response(
    *,
    intent_kind: str,
    tool_result: dict[str, Any],
) -> str:
    """Format a successful read-only tool result."""

    if intent_kind == ReadOnlyIntentKind.NATIVE_BALANCE.value:
        return (
            f"{tool_result['wallet_address']} has {tool_result['formatted']} "
            f"{tool_result['symbol']} on {tool_result['chain']}."
        )
    if intent_kind == ReadOnlyIntentKind.ERC20_BALANCE.value:
        symbol = _symbol(tool_result)
        return (
            f"{tool_result['wallet_address']} has {tool_result['formatted']} {symbol} "
            f"on {tool_result['chain']}."
        )
    if intent_kind == ReadOnlyIntentKind.ERC20_ALLOWANCE.value:
        symbol = _symbol(tool_result)
        return (
            f"{tool_result['spender_address']} is allowed to spend {tool_result['formatted']} "
            f"{symbol} from {tool_result['owner_address']} on {tool_result['chain']}."
        )
    if intent_kind == ReadOnlyIntentKind.ERC20_METADATA.value:
        name = tool_result.get("name") or "ERC20 token"
        symbol = _symbol(tool_result)
        return (
            f"{name} ({symbol}) on {tool_result['chain']} has {tool_result['decimals']} decimals."
        )
    if intent_kind == ReadOnlyIntentKind.CONTRACT_READ.value:
        return (
            f"{tool_result['function_name']} returned {tool_result['result']!r} "
            f"on {tool_result['chain']}."
        )
    if intent_kind == ReadOnlyIntentKind.KNOWN_ADDRESS.value:
        return (
            f"{tool_result['key']} ({tool_result['category']}) "
            f"on chain_id={tool_result['chain_id']} {tool_result['chain']} "
            f"resolves to {tool_result['address']}."
        )
    if intent_kind == ReadOnlyIntentKind.TOKEN_PRICES.value:
        return _format_token_prices_response(tool_result)
    return "Read-only request completed."


def _format_token_prices_response(tool_result: dict[str, Any]) -> str:
    tokens = tool_result.get("tokens")
    if not isinstance(tokens, list) or not tokens:
        return "Token prices: no results returned."

    summaries: list[str] = []
    for row in tokens:
        if not isinstance(row, dict):
            continue
        address = row.get("address", "")
        chain_label = row.get("mercury_chain") or row.get("network", "")
        err = row.get("error")
        prices = row.get("prices") if isinstance(row.get("prices"), list) else []
        if err:
            summaries.append(f"{address} on {chain_label}: error ({err})")
            continue
        if not prices:
            summaries.append(f"{address} on {chain_label}: no quoted prices")
            continue
        bits: list[str] = []
        for p in prices:
            if not isinstance(p, dict):
                continue
            cur = p.get("currency", "")
            val = p.get("value")
            bits.append(f"{val} {cur}".strip())
        price_text = ", ".join(bits) if bits else "no quoted prices"
        summaries.append(f"{address} on {chain_label}: {price_text}")

    if not summaries:
        return "Token prices: no results returned."
    return "Token prices — " + "; ".join(summaries) + "."


def _error_text(error: str | MercuryErrorInfo) -> str:
    if isinstance(error, MercuryErrorInfo):
        return error.message
    return sanitize_error(error)


def format_error_response(error: str | MercuryErrorInfo) -> str:
    """Format a sanitized error response."""

    return f"I could not complete the read-only request: {_error_text(error)}"


def format_unsupported_response(reason: str | MercuryErrorInfo) -> str:
    """Format unsupported intents without suggesting execution happened."""

    return f"Unsupported operation: {_error_text(reason)}"


def _symbol(tool_result: dict[str, Any]) -> str:
    symbol = tool_result.get("symbol")
    if isinstance(symbol, str) and symbol:
        return symbol
    return "tokens"
