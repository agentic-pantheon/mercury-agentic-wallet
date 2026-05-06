"""Routing helpers for the read-only graph."""

from mercury.graph.intents import ReadOnlyIntentKind
from mercury.graph.state import MercuryState
from mercury.models.approval import ApprovalStatus
from mercury.models.policy import PolicyDecisionStatus

ROUTE_UNSUPPORTED = "unsupported_response"
ROUTE_RESOLVE_CHAIN = "resolve_chain"
ROUTE_NATIVE_BALANCE = "get_native_balance"
ROUTE_ERC20_BALANCE = "get_erc20_balance"
ROUTE_ERC20_ALLOWANCE = "get_erc20_allowance"
ROUTE_ERC20_METADATA = "get_erc20_metadata"
ROUTE_CONTRACT_READ = "read_contract"
ROUTE_RESOLVE_KNOWN_ADDRESS = "resolve_known_address"
ROUTE_TOKEN_PRICES = "get_token_prices"
ROUTE_PORTFOLIO_TOKENS = "get_portfolio_tokens"
ROUTE_FORMAT_RESPONSE = "format_response"
ROUTE_REJECT_TRANSACTION = "reject_transaction"
ROUTE_REQUEST_APPROVAL = "request_approval"
ROUTE_CHECK_IDEMPOTENCY = "check_idempotency"
ROUTE_RESOLVE_NONCE = "resolve_nonce"
ROUTE_SIGN_TRANSACTION = "sign_transaction"
ROUTE_PREPARE_ERC20_TRANSACTION = "prepare_erc20_transaction"
ROUTE_PREPARE_NATIVE_TRANSACTION = "prepare_native_transaction"
ROUTE_PREPARE_SWAP_TRANSACTION = "prepare_swap_transaction"
ROUTE_SWAP_TYPED_ORDER_READY = "swap_typed_order_ready"

_READ_ROUTES = {
    ReadOnlyIntentKind.NATIVE_BALANCE.value: ROUTE_NATIVE_BALANCE,
    ReadOnlyIntentKind.ERC20_BALANCE.value: ROUTE_ERC20_BALANCE,
    ReadOnlyIntentKind.ERC20_ALLOWANCE.value: ROUTE_ERC20_ALLOWANCE,
    ReadOnlyIntentKind.ERC20_METADATA.value: ROUTE_ERC20_METADATA,
    ReadOnlyIntentKind.CONTRACT_READ.value: ROUTE_CONTRACT_READ,
    ReadOnlyIntentKind.KNOWN_ADDRESS.value: ROUTE_RESOLVE_KNOWN_ADDRESS,
    ReadOnlyIntentKind.TOKEN_PRICES.value: ROUTE_TOKEN_PRICES,
    ReadOnlyIntentKind.PORTFOLIO_TOKENS.value: ROUTE_PORTFOLIO_TOKENS,
}


def route_after_parse(state: MercuryState) -> str:
    """Route unsupported or invalid input away from tool execution."""

    parsed_intent = state.get("parsed_intent", {})
    if state.get("error") or parsed_intent.get("kind") == ReadOnlyIntentKind.UNSUPPORTED.value:
        return ROUTE_UNSUPPORTED
    return ROUTE_RESOLVE_CHAIN


def route_after_chain(state: MercuryState) -> str:
    """Route chain resolution errors to response formatting."""

    if state.get("error"):
        return ROUTE_FORMAT_RESPONSE
    return route_read_tool(state)


def route_read_tool(state: MercuryState) -> str:
    """Return the read-only tool node for the parsed intent."""

    parsed_intent = state.get("parsed_intent", {})
    kind = parsed_intent.get("kind")
    if isinstance(kind, str):
        return _READ_ROUTES.get(kind, ROUTE_FORMAT_RESPONSE)
    return ROUTE_FORMAT_RESPONSE


def route_after_transaction_policy(state: MercuryState) -> str:
    """Route transaction execution after policy evaluation."""

    decision = state.get("policy_decision")
    if decision is None:
        return ROUTE_REJECT_TRANSACTION
    if decision.status == PolicyDecisionStatus.REJECTED:
        return ROUTE_REJECT_TRANSACTION
    if decision.status == PolicyDecisionStatus.NEEDS_APPROVAL:
        return ROUTE_REQUEST_APPROVAL
    return ROUTE_CHECK_IDEMPOTENCY


def route_after_transaction_approval(state: MercuryState) -> str:
    """Route transaction execution after approval."""

    approval = state.get("approval_result")
    if approval is None or approval.status != ApprovalStatus.APPROVED:
        return ROUTE_REJECT_TRANSACTION
    return ROUTE_CHECK_IDEMPOTENCY


def route_after_idempotency(state: MercuryState) -> str:
    """Route duplicate idempotency outcomes away from signing."""

    if state.get("execution_result") is not None:
        return ROUTE_REJECT_TRANSACTION
    return ROUTE_SIGN_TRANSACTION
