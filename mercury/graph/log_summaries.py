"""Human-readable one-line hints for graph streaming logs (pairs with JSON payloads)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mercury.models.errors import MercuryErrorInfo
from mercury.service.logging import redact_error_message

_LOG_SNIP = 420

GRAPH_NODE_HELP: dict[str, str] = {
    "parse_intent": "parse read-only intent from user/tool payload",
    "resolve_chain": "resolve chain metadata for downstream RPC tools",
    "get_native_balance": "read native balance via RPC",
    "get_erc20_balance": "read ERC-20 balance via RPC",
    "get_erc20_allowance": "read ERC-20 allowance via RPC",
    "get_erc20_metadata": "read ERC-20 decimals/symbol via RPC",
    "read_contract": "eth_call / contract view via RPC",
    "resolve_known_address": "resolve catalog address (token, etc.)",
    "get_token_prices": "fetch spot prices (Alchemy-backed when configured)",
    "get_portfolio_tokens": "fetch wallet portfolio snapshot",
    "get_transfer_history": "fetch transfer history (Alchemy-backed when configured)",
    "format_response": "format tool output for the API response",
    "unsupported_response": "intent not supported for this graph",
    "prepare_erc20_transaction": "build ERC-20 transfer or approval transaction",
    "prepare_native_transaction": "build native (ETH) transfer transaction",
    "prepare_swap_transaction": "quote swap and build approval or swap transaction",
    "end_swap_typed_order": "typed order path (e.g. CoW) — stops before EVM tx pipeline",
    "resolve_nonce": "load sender address, chain id, and nonce",
    "populate_gas": "estimate gas and build executable transaction",
    "simulate_transaction": "simulate tx against RPC",
    "evaluate_policy": "run risk / policy rules on the simulated tx",
    "request_approval": "pause for human approval if policy requires it",
    "check_idempotency": "guard duplicate broadcasts via idempotency store",
    "sign_transaction": "sign with custody / configured signer",
    "broadcast_transaction": "submit signed tx to the network",
    "monitor_receipt": "wait for confirmations and capture receipt",
    "reject_transaction": "normalize errors or policy rejection into execution_result",
}


def graph_node_finished_summary(node_name: str, patch: Mapping[str, Any] | None) -> str:
    """Return a short explanation of what a graph step did (for log prefixes)."""

    hint = GRAPH_NODE_HELP.get(node_name, "")
    hint_part = f" — {hint}" if hint else ""

    if patch is None or not isinstance(patch, Mapping):
        return f"{node_name}: done{hint_part}"

    if "error" in patch:
        err_text = _error_snippet(patch["error"])
        return f"{node_name}: ERROR {err_text}{hint_part}"

    if "execution_result" in patch:
        ex = patch["execution_result"]
        status = _dig_str(ex, "status")
        tx = _dig_str(ex, "tx_hash")
        extra = f" tx={tx}" if tx else ""
        return f"{node_name}: execution_result status={status}{extra}{hint_part}"

    if "prepared_transaction" in patch:
        return f"{node_name}: prepared_transaction ready for pipeline{hint_part}"

    if "prepared_swap" in patch:
        return f"{node_name}: prepared_swap (quote/route){hint_part}"

    if "executable_transaction" in patch:
        return f"{node_name}: executable_transaction (gas){hint_part}"

    if "policy_decision" in patch:
        pd = patch["policy_decision"]
        st = _policy_status_snippet(pd)
        return f"{node_name}: policy_decision {st}{hint_part}"

    if "tx_hash" in patch:
        return f"{node_name}: tx_hash set{hint_part}"

    keys = ", ".join(sorted(str(k) for k in patch.keys()))
    return f"{node_name}: state update keys={keys}{hint_part}"


def graph_run_start_summary(graph_label: str, intent_kind: str) -> str:
    """One line: which subgraph runs and why."""

    return f"subgraph={graph_label} intent_kind={intent_kind}"


def invoke_intent_validation_failed_summary(intent_kind: str, *, code: str, message: str) -> str:
    """Explain validation failure before any subgraph runs."""

    clip = message if len(message) <= 240 else message[:237] + "…"
    return f"blocked kind={intent_kind} code={code}: {clip}"


def _error_snippet(err: Any) -> str:
    if isinstance(err, MercuryErrorInfo):
        text = err.message
    elif isinstance(err, dict):
        code = err.get("code", "?")
        text = f"{code}: {err.get('message', '')}"
    else:
        text = redact_error_message(str(err))
    text = str(text).strip()
    if len(text) > _LOG_SNIP:
        text = text[: _LOG_SNIP - 3] + "..."
    return text


def _dig_str(obj: Any, key: str) -> str | None:
    if obj is None:
        return None
    if hasattr(obj, key):
        raw = getattr(obj, key, None)
        if raw is not None and hasattr(raw, "value"):
            raw = raw.value
        return str(raw) if raw is not None else None
    if isinstance(obj, dict):
        raw = obj.get(key)
        return str(raw) if raw is not None else None
    return None


def _policy_status_snippet(pd: Any) -> str:
    st = getattr(pd, "status", None)
    if st is not None and hasattr(st, "value"):
        st = st.value
    if st is None and isinstance(pd, dict):
        st = pd.get("status")
    reason = getattr(pd, "reason", None) if pd is not None else None
    if reason is None and isinstance(pd, dict):
        reason = pd.get("reason")
    if reason:
        rclip = str(reason) if len(str(reason)) < 160 else str(reason)[:157] + "..."
        return f"status={st}, reason={rclip}"
    return f"status={st}"


__all__ = [
    "GRAPH_NODE_HELP",
    "graph_node_finished_summary",
    "graph_run_start_summary",
    "invoke_intent_validation_failed_summary",
]
