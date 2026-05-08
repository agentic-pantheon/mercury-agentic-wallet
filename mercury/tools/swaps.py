"""Swap preparation helpers that stop at Mercury's transaction pipeline boundary."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from mercury.chains import get_chain_by_id, get_chain_by_name
from mercury.models.addresses import normalize_evm_address
from mercury.models.erc20 import ZERO_ADDRESS, ERC20Amount
from mercury.models.execution import PreparedTransaction
from mercury.models.policy import PolicyDecision, PolicyDecisionStatus
from mercury.models.swaps import (
    SwapExecution,
    SwapExecutionType,
    SwapIntent,
    SwapProviderName,
    SwapQuote,
    SwapQuoteRequest,
)
from mercury.policy.swap_rules import (
    SwapPolicyConfig,
    evaluate_swap_execution_policy,
    evaluate_swap_quote_policy,
)
from mercury.swaps.router import SwapRouter
from mercury.tools.erc20 import get_erc20_metadata
from mercury.tools.erc20_transactions import (
    PublicAddressResolver,
    check_erc20_approval_preconditions,
    prepare_erc20_approval,
)
from mercury.tools.evm import ProviderFactoryLike

NATIVE_SELL_DECIMALS = 18


class SwapAllowanceCheck(BaseModel):
    """Allowance state for a quoted swap spender."""

    model_config = ConfigDict(frozen=True)

    spender_address: str
    required_amount_raw: int = Field(gt=0)
    current_allowance_raw: int = Field(ge=0)
    allowance_sufficient: bool


class PreparedSwap(BaseModel):
    """Swap preparation result before the generic signer/broadcast pipeline."""

    model_config = ConfigDict(frozen=True)

    quote: SwapQuote
    execution: SwapExecution | None = None
    quote_policy_decision: PolicyDecision
    execution_policy_decision: PolicyDecision | None = None
    allowance: SwapAllowanceCheck | None = None
    approval_transaction: PreparedTransaction | None = None
    swap_transaction: PreparedTransaction | None = None
    order_submission_idempotency_key: str | None = None

    @property
    def next_transaction(self) -> PreparedTransaction | None:
        """Return the transaction that must enter the Phase 6 pipeline next."""

        return self.approval_transaction or self.swap_transaction


def prepare_swap(
    *,
    intent: SwapIntent,
    router: SwapRouter,
    provider_factory: ProviderFactoryLike,
    address_resolver: PublicAddressResolver,
    policy_config: SwapPolicyConfig | None = None,
) -> PreparedSwap:
    """Prepare either an ERC20 approval or a swap transaction for the safe pipeline."""

    wallet = address_resolver.get_wallet_address(intent.wallet_id)
    chain = get_chain_by_name(intent.chain)
    to_chain: str | None = None
    to_chain_id: int | None = None
    if intent.to_chain is not None:
        dest = get_chain_by_name(intent.to_chain)
        if intent.to_chain_id is not None and dest.chain_id != intent.to_chain_id:
            raise ValueError("Destination chain name does not match destination chain id.")
        to_chain_id = dest.chain_id
        to_chain = dest.name
    elif intent.to_chain_id is not None:
        dest = get_chain_by_id(intent.to_chain_id)
        to_chain_id = dest.chain_id
        to_chain = dest.name

    canonical_native = normalize_evm_address(ZERO_ADDRESS)
    from_token_is_native = intent.from_token == canonical_native
    if from_token_is_native:
        if chain.wrapped_native_token_address is None:
            raise ValueError(
                f"Cannot prepare a native token swap on chain {chain.name!r}: "
                "wrapped native token metadata is not configured for this chain."
            )
        wrapped_from_token = normalize_evm_address(chain.wrapped_native_token_address)
        amount = ERC20Amount.from_human(intent.amount_in, NATIVE_SELL_DECIMALS)
        from_token_for_request = canonical_native
    else:
        token_metadata = get_erc20_metadata(
            chain=chain.name,
            token_address=intent.from_token,
            provider_factory=provider_factory,
        )
        amount = ERC20Amount.from_human(intent.amount_in, token_metadata.decimals)
        from_token_for_request = token_metadata.token_address
        wrapped_from_token = None

    if amount.raw_amount <= 0:
        raise ValueError("Swap amount must be greater than zero.")

    request = SwapQuoteRequest(
        wallet_id=intent.wallet_id,
        wallet_address=wallet.address,
        chain=chain.name,
        chain_id=chain.chain_id,
        from_token=from_token_for_request,
        to_token=intent.to_token,
        amount_in=intent.amount_in,
        amount_in_raw=amount.raw_amount,
        max_slippage_bps=intent.max_slippage_bps,
        min_amount_out=intent.min_amount_out,
        recipient_address=intent.recipient_address,
        idempotency_key=intent.idempotency_key,
        to_chain=to_chain,
        to_chain_id=to_chain_id,
        from_token_is_native=from_token_is_native,
        wrapped_from_token=wrapped_from_token,
    )
    quote = router.get_quote(request, provider_preference=intent.provider_preference)
    quote_decision = evaluate_swap_quote_policy(quote, config=policy_config)
    if quote_decision.status == PolicyDecisionStatus.REJECTED:
        return PreparedSwap(quote=quote, quote_policy_decision=quote_decision)

    allowance = None
    if not quote.request.from_token_is_native:
        allowance = check_swap_allowance(
            quote=quote,
            provider_factory=provider_factory,
        )
        if not allowance.allowance_sufficient:
            approval = prepare_erc20_approval(
                chain=quote.request.chain,
                wallet_id=quote.request.wallet_id,
                token_address=quote.route.from_token,
                spender_address=allowance.spender_address,
                amount=quote.request.amount_in,
                provider_factory=provider_factory,
                address_resolver=address_resolver,
                idempotency_key=f"{quote.request.idempotency_key}:approval",
                spender_known=True,
            )
            return PreparedSwap(
                quote=quote,
                quote_policy_decision=quote_decision,
                allowance=allowance,
                approval_transaction=approval,
            )

    execution = router.provider_for(quote.provider).build_execution(quote)
    execution_decision = evaluate_swap_execution_policy(execution, config=policy_config)
    if execution_decision.status == PolicyDecisionStatus.REJECTED:
        return PreparedSwap(
            quote=quote,
            execution=execution,
            quote_policy_decision=quote_decision,
            execution_policy_decision=execution_decision,
            allowance=allowance,
        )
    if (
        execution.execution_type == SwapExecutionType.EIP712_ORDER
        and execution.provider == SwapProviderName.COWSWAP
    ):
        return PreparedSwap(
            quote=quote,
            execution=execution,
            quote_policy_decision=quote_decision,
            execution_policy_decision=execution_decision,
            allowance=allowance,
            order_submission_idempotency_key=f"{quote.request.idempotency_key}:cow_order",
        )
    return PreparedSwap(
        quote=quote,
        execution=execution,
        quote_policy_decision=quote_decision,
        execution_policy_decision=execution_decision,
        allowance=allowance,
        swap_transaction=prepared_swap_transaction_from_execution(execution),
    )


def check_swap_allowance(
    *,
    quote: SwapQuote,
    provider_factory: ProviderFactoryLike,
) -> SwapAllowanceCheck:
    """Check allowance for the provider-declared spender before swap execution."""

    if quote.route.spender_address is None:
        raise ValueError("Swap quote is missing spender address.")
    preconditions = check_erc20_approval_preconditions(
        chain=quote.request.chain,
        token_address=quote.route.from_token,
        owner_address=quote.request.wallet_address,
        spender_address=quote.route.spender_address,
        amount=quote.request.amount_in,
        provider_factory=provider_factory,
        spender_known=True,
    )
    return SwapAllowanceCheck(
        spender_address=preconditions.spender_address,
        required_amount_raw=preconditions.amount.raw_amount,
        current_allowance_raw=preconditions.current_allowance_raw,
        allowance_sufficient=preconditions.allowance_sufficient,
    )


def prepared_swap_transaction_from_execution(execution: SwapExecution) -> PreparedTransaction:
    """Convert an EVM swap execution into the generic transaction pipeline model."""

    if (
        execution.execution_type != SwapExecutionType.EVM_TRANSACTION
        or execution.transaction is None
    ):
        raise ValueError("Only EVM swap executions can become prepared transactions.")
    quote = execution.quote
    return PreparedTransaction(
        wallet_id=quote.request.wallet_id,
        chain=quote.request.chain,
        chain_id=execution.transaction.chain_id,
        from_address=quote.request.wallet_address,
        to=execution.transaction.to,
        value_wei=execution.transaction.value_wei,
        data=execution.transaction.data,
        idempotency_key=quote.request.idempotency_key,
        metadata={
            "action": "swap",
            "provider": execution.provider.value,
            "route_id": quote.route.route_id,
            "route_kind": quote.route.route_kind.value,
            "from_token": quote.route.from_token,
            "to_token": quote.route.to_token,
            "spender_address": quote.route.spender_address,
            "amount_in": quote.request.amount_in,
            "amount_in_raw": quote.amount_in_raw,
            "expected_amount_out_raw": quote.expected_amount_out_raw,
            "min_amount_out_raw": quote.min_amount_out_raw,
            "slippage_bps": quote.slippage_bps,
            "recipient_address": quote.recipient_address,
            "quote_expires_at": quote.expires_at.isoformat() if quote.expires_at else None,
        },
    )
