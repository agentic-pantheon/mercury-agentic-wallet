"""Swap-specific policy checks for normalized provider responses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from mercury.chains import UnsupportedChainError, get_chain_by_id, get_chain_by_name
from mercury.models.addresses import normalize_evm_address
from mercury.models.execution import ExecutableTransaction, PreparedTransaction
from mercury.models.policy import PolicyDecision, PolicyDecisionStatus
from mercury.models.swaps import (
    SwapExecution,
    SwapExecutionType,
    SwapProviderName,
    SwapQuote,
    SwapRouteKind,
)


@dataclass(frozen=True)
class SwapPolicyConfig:
    """Conservative swap policy defaults for Phase 8."""

    allowed_providers: frozenset[SwapProviderName] = frozenset(
        {
            SwapProviderName.LIFI,
            SwapProviderName.COWSWAP,
            SwapProviderName.UNISWAP,
        }
    )
    max_slippage_bps: int = 100
    allow_bridges: bool = True


def evaluate_swap_quote_policy(
    quote: SwapQuote,
    *,
    config: SwapPolicyConfig | None = None,
    now: datetime | None = None,
) -> PolicyDecision:
    """Evaluate provider quote safety before allowance or transaction building."""

    reason = swap_quote_rejection_reason(quote, config=config, now=now)
    if reason is not None:
        return PolicyDecision(status=PolicyDecisionStatus.REJECTED, reason=reason)
    return PolicyDecision(
        status=PolicyDecisionStatus.ALLOWED,
        reason="Swap quote passed policy checks.",
    )


def evaluate_swap_execution_policy(
    execution: SwapExecution,
    *,
    config: SwapPolicyConfig | None = None,
    now: datetime | None = None,
) -> PolicyDecision:
    """Evaluate provider execution payload before feeding any signing pipeline."""

    quote_reason = swap_quote_rejection_reason(execution.quote, config=config, now=now)
    if quote_reason is not None:
        return PolicyDecision(status=PolicyDecisionStatus.REJECTED, reason=quote_reason)
    reason = swap_execution_rejection_reason(execution)
    if reason is not None:
        return PolicyDecision(status=PolicyDecisionStatus.REJECTED, reason=reason)
    return PolicyDecision(
        status=PolicyDecisionStatus.NEEDS_APPROVAL,
        reason="Human approval is required for swap execution.",
    )


def swap_quote_rejection_reason(
    quote: SwapQuote,
    *,
    config: SwapPolicyConfig | None = None,
    now: datetime | None = None,
) -> str | None:
    """Return a rejection reason for unsafe normalized quotes."""

    policy = config or SwapPolicyConfig()
    current_time = now or datetime.now(tz=UTC)
    if quote.provider not in policy.allowed_providers:
        return "Swap provider is not in the allowlist."
    if quote.slippage_bps is not None and quote.slippage_bps > policy.max_slippage_bps:
        return "Swap slippage exceeds configured maximum."
    if quote.expires_at is not None and quote.expires_at <= current_time:
        return "Swap quote is expired."
    try:
        chain = get_chain_by_name(quote.request.chain)
    except UnsupportedChainError as exc:
        return str(exc)
    if chain.chain_id != quote.request.chain_id:
        return "Swap request chain_id does not match resolved chain."
    if quote.route.from_chain_id != quote.request.chain_id:
        return "Swap route source chain does not match requested chain."
    if (
        quote.route.to_chain_id != quote.request.chain_id
        and quote.route.route_kind != SwapRouteKind.BRIDGE
    ):
        return "Swap route destination chain mismatch is not marked as a bridge."
    if quote.route.route_kind == SwapRouteKind.BRIDGE:
        try:
            get_chain_by_id(quote.route.to_chain_id)
        except UnsupportedChainError as exc:
            return str(exc)
        if not policy.allow_bridges:
            return "Bridge routes require explicit user approval and are disabled by default."
    if quote.route.spender_address is None:
        return "Swap route spender is missing."
    if (
        quote.min_amount_out_raw is not None
        and quote.expected_amount_out_raw < quote.min_amount_out_raw
    ):
        return "Swap quote expected output is below the minimum output."
    if quote.request.min_amount_out is not None and quote.min_amount_out_raw is None:
        return "Swap quote is missing minimum output protection."
    if quote.recipient_address != quote.request.wallet_address:
        try:
            normalize_evm_address(quote.recipient_address)
        except ValueError:
            return "Swap recipient address is invalid."
    return None


def swap_execution_rejection_reason(execution: SwapExecution) -> str | None:
    """Return a rejection reason for unsafe normalized execution payloads."""

    if execution.execution_type == SwapExecutionType.UNSUPPORTED:
        return execution.unsupported_reason or "Swap execution is unsupported."
    if execution.execution_type == SwapExecutionType.EIP712_ORDER:
        return _swap_cow_typed_order_rejection_reason(execution)
    if execution.transaction is None:
        return "Swap execution is missing an EVM transaction."
    if execution.transaction.chain_id != execution.quote.request.chain_id:
        return "Swap execution chain_id does not match quote chain."
    return None


def _swap_cow_typed_order_rejection_reason(execution: SwapExecution) -> str | None:
    """EIP-712 is only allowed for CoW Swap with a complete typed order payload."""

    if execution.provider != SwapProviderName.COWSWAP:
        return "Swap typed order execution requires a dedicated signer approval path."
    if execution.order is None:
        return "CoW typed order execution is missing order metadata."
    if execution.order.chain_id != execution.quote.request.chain_id:
        return "CoW typed order chain_id does not match the quote request."
    if not execution.order.typed_data:
        return "CoW typed order is missing signing payload (typed data)."
    if execution.order.submit_url is None or not str(execution.order.submit_url).strip():
        return "CoW typed order is missing a submission URL."
    return None


def swap_transaction_policy_reason(
    transaction: ExecutableTransaction | PreparedTransaction,
    *,
    config: SwapPolicyConfig | None = None,
) -> str | None:
    """Return a rejection reason for swap transactions in the generic pipeline."""

    if transaction.metadata.get("action") != "swap":
        return None
    policy = config or SwapPolicyConfig()
    provider_value = transaction.metadata.get("provider")
    try:
        provider = SwapProviderName(str(provider_value))
    except ValueError:
        return "Swap transaction provider is not recognized."
    if provider not in policy.allowed_providers:
        return "Swap provider is not in the allowlist."
    spender = transaction.metadata.get("spender_address")
    if not isinstance(spender, str) or not spender.strip():
        return "Swap transaction spender is missing."
    try:
        normalize_evm_address(spender)
        normalize_evm_address(transaction.to)
        recipient = transaction.metadata.get("recipient_address")
        if isinstance(recipient, str):
            normalize_evm_address(recipient)
    except ValueError:
        return "Swap transaction contains an invalid address."
    slippage = transaction.metadata.get("slippage_bps")
    if isinstance(slippage, int) and slippage > policy.max_slippage_bps:
        return "Swap slippage exceeds configured maximum."
    return None


def swap_approval_reason(transaction: ExecutableTransaction) -> str | None:
    """Return the required human approval reason for swap transactions."""

    if transaction.metadata.get("action") == "swap":
        return "Human approval is required for swap execution."
    return None
