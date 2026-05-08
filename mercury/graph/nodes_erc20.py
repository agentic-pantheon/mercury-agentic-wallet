"""LangGraph nodes for preparing ERC20 transactions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mercury.graph.preparation_helpers import state_for_preparation_error
from mercury.graph.request_metadata import merge_intent_metadata_into_prepared
from mercury.graph.state import MercuryState
from mercury.models.erc20 import (
    ERC20ApprovalIntent,
    ERC20TransferIntent,
)
from mercury.tools.erc20_transactions import (
    PublicAddressResolver,
    prepare_erc20_approval,
    prepare_erc20_transfer,
)
from mercury.tools.evm import ProviderFactoryLike


@dataclass(frozen=True)
class ERC20GraphDependencies:
    """Dependencies required to prepare ERC20 transactions before Phase 6 execution."""

    provider_factory: ProviderFactoryLike
    address_resolver: PublicAddressResolver


def make_erc20_prepare_node(deps: ERC20GraphDependencies) -> Callable[[MercuryState], MercuryState]:
    """Create a node that converts an ERC20 intent into a prepared transaction."""

    def prepare_erc20_transaction(state: MercuryState) -> MercuryState:
        try:
            payload = _erc20_payload_from_state(state)
            kind = str(payload.get("kind", "")).lower()
            if kind == "erc20_transfer":
                transfer_intent = ERC20TransferIntent.model_validate(payload)
                prepared = prepare_erc20_transfer(
                    chain=transfer_intent.chain,
                    wallet_id=transfer_intent.wallet_id,
                    token_address=transfer_intent.token_address,
                    recipient_address=transfer_intent.recipient_address,
                    amount=transfer_intent.amount,
                    provider_factory=deps.provider_factory,
                    address_resolver=deps.address_resolver,
                    idempotency_key=transfer_intent.idempotency_key,
                    amount_in_smallest_units=transfer_intent.amount_in_smallest_units,
                )
            elif kind == "erc20_approval":
                approval_intent = ERC20ApprovalIntent.model_validate(payload)
                prepared = prepare_erc20_approval(
                    chain=approval_intent.chain,
                    wallet_id=approval_intent.wallet_id,
                    token_address=approval_intent.token_address,
                    spender_address=approval_intent.spender_address,
                    amount=approval_intent.amount,
                    provider_factory=deps.provider_factory,
                    address_resolver=deps.address_resolver,
                    idempotency_key=approval_intent.idempotency_key,
                    spender_known=approval_intent.spender_known,
                    allow_unlimited=approval_intent.allow_unlimited,
                    amount_in_smallest_units=approval_intent.amount_in_smallest_units,
                )
            else:
                raise ValueError(f"Unsupported ERC20 transaction intent: {kind}.")
        except Exception as exc:
            return state_for_preparation_error(exc, stage="prepare_erc20_transaction")

        prepared = merge_intent_metadata_into_prepared(prepared, payload)
        return {
            "prepared_transaction": prepared,
            "chain_name": prepared.chain,
            "wallet_address": prepared.from_address or "",
        }

    return prepare_erc20_transaction


def route_erc20_intent(state: MercuryState) -> str:
    """Route structured ERC20 intents to the transfer or approval builder."""

    try:
        payload = _erc20_payload_from_state(state)
    except ValueError:
        return "unsupported_response"
    kind = str(payload.get("kind", "")).lower()
    if kind in {"erc20_transfer", "erc20_approval"}:
        return "prepare_erc20_transaction"
    return "unsupported_response"


def route_after_erc20_prepare(state: MercuryState) -> str:
    """Route ERC20 preparation errors away from nonce, gas, signing, and broadcast."""

    if state.get("error"):
        return "reject_transaction"
    return "resolve_nonce"


def _erc20_payload_from_state(state: MercuryState) -> dict[str, Any]:
    raw = state.get("parsed_intent", state.get("raw_input"))
    if isinstance(raw, dict):
        intent = raw.get("intent")
        return intent if isinstance(intent, dict) else raw
    raise ValueError("ERC20 graph requires a structured transaction intent.")
