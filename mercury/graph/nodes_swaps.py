"""LangGraph nodes for swap preparation before transaction execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from mercury.graph.preparation_helpers import state_for_preparation_error
from mercury.graph.request_metadata import merge_intent_metadata_into_prepared
from mercury.graph.router import (
    ROUTE_REJECT_TRANSACTION,
    ROUTE_RESOLVE_NONCE,
    ROUTE_SWAP_TYPED_ORDER_READY,
)
from mercury.graph.state import MercuryState
from mercury.models.policy import PolicyDecision, PolicyDecisionStatus
from mercury.models.swaps import SwapExecutionType, SwapIntent
from mercury.policy.swap_rules import SwapPolicyConfig
from mercury.swaps.router import SwapRouter
from mercury.tools.erc20_transactions import PublicAddressResolver
from mercury.tools.evm import ProviderFactoryLike
from mercury.tools.swaps import prepare_swap


@dataclass(frozen=True)
class SwapGraphDependencies:
    """Dependencies required to prepare swaps before the Phase 6 pipeline."""

    router: SwapRouter
    provider_factory: ProviderFactoryLike
    address_resolver: PublicAddressResolver
    policy_config: SwapPolicyConfig = field(default_factory=SwapPolicyConfig)


def make_swap_prepare_node(deps: SwapGraphDependencies) -> Callable[[MercuryState], MercuryState]:
    """Create a node that converts a swap intent into the next safe transaction."""

    def prepare_swap_transaction(state: MercuryState) -> MercuryState:
        try:
            raw_payload = _swap_payload_from_state(state)
            intent = SwapIntent.model_validate(raw_payload)
            prepared = prepare_swap(
                intent=intent,
                router=deps.router,
                provider_factory=deps.provider_factory,
                address_resolver=deps.address_resolver,
                policy_config=deps.policy_config,
            )
        except Exception as exc:
            return state_for_preparation_error(exc, stage="prepare_swap_transaction")

        updates: MercuryState = {
            "prepared_swap": prepared,
            "chain_name": prepared.quote.request.chain,
            "wallet_address": prepared.quote.request.wallet_address,
        }
        if prepared.next_transaction is not None:
            merged_tx = merge_intent_metadata_into_prepared(
                prepared.next_transaction,
                raw_payload,
            )
            updates["prepared_transaction"] = merged_tx
            return updates

        if (
            prepared.execution is not None
            and prepared.execution.execution_type == SwapExecutionType.EIP712_ORDER
            and prepared.execution_policy_decision is not None
            and prepared.execution_policy_decision.status == PolicyDecisionStatus.NEEDS_APPROVAL
        ):
            updates["policy_decision"] = prepared.execution_policy_decision
            return updates

        decision = prepared.execution_policy_decision or prepared.quote_policy_decision
        if decision.status != PolicyDecisionStatus.REJECTED:
            decision = PolicyDecision(
                status=PolicyDecisionStatus.REJECTED,
                reason="Swap did not produce a transaction for execution.",
            )
        updates["policy_decision"] = decision
        return updates

    return prepare_swap_transaction


def end_swap_typed_order_pipeline(state: MercuryState) -> MercuryState:
    """Typed CoW orders stop here until a dedicated EIP-712 signing path exists."""

    return {}


def route_swap_intent(state: MercuryState) -> str:
    """Route structured swap intents to the swap builder."""

    try:
        payload = _swap_payload_from_state(state)
    except ValueError:
        return "unsupported_response"
    if str(payload.get("kind", "")).lower() == "swap":
        return "prepare_swap_transaction"
    return "unsupported_response"


def route_after_swap_prepare(state: MercuryState) -> str:
    """Route EVM swaps into the tx pipeline; typed CoW orders to a terminal node."""

    if state.get("error"):
        return ROUTE_REJECT_TRANSACTION
    if state.get("prepared_transaction") is not None:
        return ROUTE_RESOLVE_NONCE
    prepared = state.get("prepared_swap")
    if prepared is not None:
        ex = prepared.execution
        ex_dec = prepared.execution_policy_decision
        if (
            ex is not None
            and ex.execution_type == SwapExecutionType.EIP712_ORDER
            and ex_dec is not None
            and ex_dec.status == PolicyDecisionStatus.NEEDS_APPROVAL
        ):
            return ROUTE_SWAP_TYPED_ORDER_READY
    return ROUTE_REJECT_TRANSACTION


def _swap_payload_from_state(state: MercuryState) -> dict[str, Any]:
    raw = state.get("parsed_intent", state.get("raw_input"))
    if isinstance(raw, dict):
        intent = raw.get("intent")
        return intent if isinstance(intent, dict) else raw
    raise ValueError("Swap graph requires a structured swap intent.")
