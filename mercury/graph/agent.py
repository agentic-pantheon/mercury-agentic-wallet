"""LangGraph construction for Mercury read-only execution."""

from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from mercury.graph.nodes import (
    format_response,
    make_read_tool_node,
    parse_intent,
    resolve_chain,
    unsupported_response,
)
from mercury.graph.nodes_erc20 import (
    ERC20GraphDependencies,
    make_erc20_prepare_node,
    route_after_erc20_prepare,
    route_erc20_intent,
)
from mercury.graph.nodes_native import (
    NativeGraphDependencies,
    make_native_prepare_node,
    route_after_native_prepare,
    route_native_intent,
)
from mercury.graph.nodes_swaps import (
    SwapGraphDependencies,
    end_swap_typed_order_pipeline,
    make_swap_prepare_node,
    route_after_swap_prepare,
    route_swap_intent,
)
from mercury.graph.nodes_transaction import (
    TransactionGraphDependencies,
    make_approval_node,
    make_broadcast_transaction_node,
    make_idempotency_node,
    make_monitor_receipt_node,
    make_policy_node,
    make_populate_gas_node,
    make_resolve_nonce_node,
    make_sign_transaction_node,
    make_simulate_transaction_node,
    reject_transaction,
)
from mercury.graph.router import (
    ROUTE_CHECK_IDEMPOTENCY,
    ROUTE_CONTRACT_READ,
    ROUTE_ERC20_ALLOWANCE,
    ROUTE_ERC20_BALANCE,
    ROUTE_ERC20_METADATA,
    ROUTE_FORMAT_RESPONSE,
    ROUTE_NATIVE_BALANCE,
    ROUTE_PREPARE_ERC20_TRANSACTION,
    ROUTE_PREPARE_NATIVE_TRANSACTION,
    ROUTE_PREPARE_SWAP_TRANSACTION,
    ROUTE_PORTFOLIO_TOKENS,
    ROUTE_REJECT_TRANSACTION,
    ROUTE_REQUEST_APPROVAL,
    ROUTE_RESOLVE_CHAIN,
    ROUTE_RESOLVE_KNOWN_ADDRESS,
    ROUTE_RESOLVE_NONCE,
    ROUTE_SIGN_TRANSACTION,
    ROUTE_SWAP_TYPED_ORDER_READY,
    ROUTE_TOKEN_PRICES,
    ROUTE_TRANSFER_HISTORY,
    ROUTE_UNSUPPORTED,
    route_after_chain,
    route_after_idempotency,
    route_after_parse,
    route_after_transaction_approval,
    route_after_transaction_policy,
)
from mercury.graph.state import MercuryState
from mercury.tools.registry import ReadOnlyToolRegistry


def build_graph(registry: ReadOnlyToolRegistry | None = None) -> StateGraph[MercuryState]:
    """Build the uncompiled read-only graph.

    Callers may inject a registry backed by fake tools or providers. The module-level
    graph intentionally compiles without tools so imports never require live RPC.
    """

    tool_registry = registry or ReadOnlyToolRegistry()

    builder = StateGraph(MercuryState)
    builder.add_node("parse_intent", parse_intent)
    builder.add_node("resolve_chain", resolve_chain)
    builder.add_node("unsupported_response", unsupported_response)
    builder.add_node(
        "get_native_balance",
        cast(Any, make_read_tool_node("get_native_balance", tool_registry)),
    )
    builder.add_node(
        "get_erc20_balance",
        cast(Any, make_read_tool_node("get_erc20_balance", tool_registry)),
    )
    builder.add_node(
        "get_erc20_allowance",
        cast(Any, make_read_tool_node("get_erc20_allowance", tool_registry)),
    )
    builder.add_node(
        "get_erc20_metadata",
        cast(Any, make_read_tool_node("get_erc20_metadata", tool_registry)),
    )
    builder.add_node(
        "read_contract",
        cast(Any, make_read_tool_node("read_contract", tool_registry)),
    )
    builder.add_node(
        "resolve_known_address",
        cast(Any, make_read_tool_node("resolve_known_address", tool_registry)),
    )
    builder.add_node(
        "get_token_prices",
        cast(Any, make_read_tool_node("get_token_prices", tool_registry)),
    )
    builder.add_node(
        "get_portfolio_tokens",
        cast(Any, make_read_tool_node("get_portfolio_tokens", tool_registry)),
    )
    builder.add_node(
        "get_transfer_history",
        cast(Any, make_read_tool_node("get_transfer_history", tool_registry)),
    )
    builder.add_node("format_response", format_response)

    builder.add_edge(START, "parse_intent")
    builder.add_conditional_edges(
        "parse_intent",
        route_after_parse,
        {
            ROUTE_UNSUPPORTED: "unsupported_response",
            ROUTE_RESOLVE_CHAIN: "resolve_chain",
        },
    )
    builder.add_conditional_edges(
        "resolve_chain",
        route_after_chain,
        {
            ROUTE_NATIVE_BALANCE: "get_native_balance",
            ROUTE_ERC20_BALANCE: "get_erc20_balance",
            ROUTE_ERC20_ALLOWANCE: "get_erc20_allowance",
            ROUTE_ERC20_METADATA: "get_erc20_metadata",
            ROUTE_CONTRACT_READ: "read_contract",
            ROUTE_RESOLVE_KNOWN_ADDRESS: "resolve_known_address",
            ROUTE_TOKEN_PRICES: "get_token_prices",
            ROUTE_PORTFOLIO_TOKENS: "get_portfolio_tokens",
            ROUTE_TRANSFER_HISTORY: "get_transfer_history",
            ROUTE_FORMAT_RESPONSE: "format_response",
        },
    )
    builder.add_edge("get_native_balance", "format_response")
    builder.add_edge("get_erc20_balance", "format_response")
    builder.add_edge("get_erc20_allowance", "format_response")
    builder.add_edge("get_erc20_metadata", "format_response")
    builder.add_edge("read_contract", "format_response")
    builder.add_edge("resolve_known_address", "format_response")
    builder.add_edge("get_token_prices", "format_response")
    builder.add_edge("get_portfolio_tokens", "format_response")
    builder.add_edge("get_transfer_history", "format_response")
    builder.add_edge("unsupported_response", END)
    builder.add_edge("format_response", END)

    return builder


def build_transaction_graph(deps: TransactionGraphDependencies) -> StateGraph[MercuryState]:
    """Build the uncompiled generic value-moving transaction graph."""

    builder = StateGraph(MercuryState)
    _add_transaction_pipeline(builder, deps)

    builder.add_edge(START, "resolve_nonce")

    return builder


def build_erc20_transaction_graph(
    erc20_deps: ERC20GraphDependencies,
    transaction_deps: TransactionGraphDependencies,
) -> StateGraph[MercuryState]:
    """Build an ERC20 preparation graph that feeds the generic transaction pipeline."""

    builder = StateGraph(MercuryState)
    builder.add_node("prepare_erc20_transaction", cast(Any, make_erc20_prepare_node(erc20_deps)))
    builder.add_node("unsupported_response", unsupported_response)
    _add_transaction_pipeline(builder, transaction_deps)

    builder.add_conditional_edges(
        START,
        route_erc20_intent,
        {
            ROUTE_PREPARE_ERC20_TRANSACTION: "prepare_erc20_transaction",
            ROUTE_UNSUPPORTED: "unsupported_response",
        },
    )
    builder.add_conditional_edges(
        "prepare_erc20_transaction",
        route_after_erc20_prepare,
        {
            ROUTE_REJECT_TRANSACTION: "reject_transaction",
            ROUTE_RESOLVE_NONCE: "resolve_nonce",
        },
    )
    builder.add_edge("unsupported_response", END)

    return builder


def build_native_transaction_graph(
    native_deps: NativeGraphDependencies,
    transaction_deps: TransactionGraphDependencies,
) -> StateGraph[MercuryState]:
    """Build a native transfer preparation graph that feeds the generic transaction pipeline."""

    builder = StateGraph(MercuryState)
    builder.add_node("prepare_native_transaction", cast(Any, make_native_prepare_node(native_deps)))
    builder.add_node("unsupported_response", unsupported_response)
    _add_transaction_pipeline(builder, transaction_deps)

    builder.add_conditional_edges(
        START,
        route_native_intent,
        {
            ROUTE_PREPARE_NATIVE_TRANSACTION: "prepare_native_transaction",
            ROUTE_UNSUPPORTED: "unsupported_response",
        },
    )
    builder.add_conditional_edges(
        "prepare_native_transaction",
        route_after_native_prepare,
        {
            ROUTE_REJECT_TRANSACTION: "reject_transaction",
            ROUTE_RESOLVE_NONCE: "resolve_nonce",
        },
    )
    builder.add_edge("unsupported_response", END)

    return builder


def build_swap_transaction_graph(
    swap_deps: SwapGraphDependencies,
    transaction_deps: TransactionGraphDependencies,
) -> StateGraph[MercuryState]:
    """Build a swap preparation graph that feeds the generic transaction pipeline."""

    builder = StateGraph(MercuryState)
    builder.add_node("prepare_swap_transaction", cast(Any, make_swap_prepare_node(swap_deps)))
    builder.add_node("unsupported_response", unsupported_response)
    builder.add_node("end_swap_typed_order", cast(Any, end_swap_typed_order_pipeline))
    _add_transaction_pipeline(builder, transaction_deps)

    builder.add_conditional_edges(
        START,
        route_swap_intent,
        {
            ROUTE_PREPARE_SWAP_TRANSACTION: "prepare_swap_transaction",
            ROUTE_UNSUPPORTED: "unsupported_response",
        },
    )
    builder.add_conditional_edges(
        "prepare_swap_transaction",
        route_after_swap_prepare,
        {
            ROUTE_REJECT_TRANSACTION: "reject_transaction",
            ROUTE_RESOLVE_NONCE: "resolve_nonce",
            ROUTE_SWAP_TYPED_ORDER_READY: "end_swap_typed_order",
        },
    )
    builder.add_edge("unsupported_response", END)
    builder.add_edge("end_swap_typed_order", END)

    return builder


def _add_transaction_pipeline(
    builder: StateGraph[MercuryState],
    deps: TransactionGraphDependencies,
) -> None:
    """Add the Phase 6 transaction execution pipeline nodes to a graph."""

    builder.add_node("resolve_nonce", cast(Any, make_resolve_nonce_node(deps)))
    builder.add_node("populate_gas", cast(Any, make_populate_gas_node(deps)))
    builder.add_node("simulate_transaction", cast(Any, make_simulate_transaction_node(deps)))
    builder.add_node("evaluate_policy", cast(Any, make_policy_node(deps)))
    builder.add_node("request_approval", cast(Any, make_approval_node(deps)))
    builder.add_node("check_idempotency", cast(Any, make_idempotency_node(deps)))
    builder.add_node("sign_transaction", cast(Any, make_sign_transaction_node(deps)))
    builder.add_node("broadcast_transaction", cast(Any, make_broadcast_transaction_node(deps)))
    builder.add_node("monitor_receipt", cast(Any, make_monitor_receipt_node(deps)))
    builder.add_node("reject_transaction", reject_transaction)

    builder.add_edge("resolve_nonce", "populate_gas")
    builder.add_edge("populate_gas", "simulate_transaction")
    builder.add_edge("simulate_transaction", "evaluate_policy")
    builder.add_conditional_edges(
        "evaluate_policy",
        route_after_transaction_policy,
        {
            ROUTE_REJECT_TRANSACTION: "reject_transaction",
            ROUTE_REQUEST_APPROVAL: "request_approval",
            ROUTE_CHECK_IDEMPOTENCY: "check_idempotency",
        },
    )
    builder.add_conditional_edges(
        "request_approval",
        route_after_transaction_approval,
        {
            ROUTE_REJECT_TRANSACTION: "reject_transaction",
            ROUTE_CHECK_IDEMPOTENCY: "check_idempotency",
        },
    )
    builder.add_conditional_edges(
        "check_idempotency",
        route_after_idempotency,
        {
            ROUTE_REJECT_TRANSACTION: "reject_transaction",
            ROUTE_SIGN_TRANSACTION: "sign_transaction",
        },
    )
    builder.add_edge("sign_transaction", "broadcast_transaction")
    builder.add_edge("broadcast_transaction", "monitor_receipt")
    builder.add_edge("monitor_receipt", END)
    builder.add_edge("reject_transaction", END)


graph = build_graph().compile()
