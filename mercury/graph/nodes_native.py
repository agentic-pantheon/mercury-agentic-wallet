"""LangGraph nodes for preparing native (gas token) transfers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mercury.graph.preparation_helpers import state_for_preparation_error
from mercury.graph.request_metadata import merge_intent_metadata_into_prepared
from mercury.graph.state import MercuryState
from mercury.models.native_tx import NativeTransferIntent
from mercury.tools.erc20_transactions import PublicAddressResolver
from mercury.tools.native_transactions import prepare_native_transfer


@dataclass(frozen=True)
class NativeGraphDependencies:
    """Dependencies to prepare native transfers before the transaction pipeline."""

    address_resolver: PublicAddressResolver


def make_native_prepare_node(
    deps: NativeGraphDependencies,
) -> Callable[[MercuryState], MercuryState]:
    """Create a node that converts a native_transfer intent into a prepared transaction."""

    def prepare_native_transaction(state: MercuryState) -> MercuryState:
        try:
            payload = _native_payload_from_state(state)
            intent = NativeTransferIntent.model_validate(payload)
            prepared = prepare_native_transfer(
                chain=intent.chain,
                wallet_id=intent.wallet_id,
                recipient_address=intent.recipient_address,
                amount=intent.amount,
                address_resolver=deps.address_resolver,
                idempotency_key=intent.idempotency_key,
            )
        except Exception as exc:
            return state_for_preparation_error(exc, stage="prepare_native_transaction")

        prepared = merge_intent_metadata_into_prepared(prepared, payload)
        return {
            "prepared_transaction": prepared,
            "chain_name": prepared.chain,
            "wallet_address": prepared.from_address or "",
        }

    return prepare_native_transaction


def route_native_intent(state: MercuryState) -> str:
    """Route native_transfer intents to the native prepare node."""

    try:
        payload = _native_payload_from_state(state)
    except ValueError:
        return "unsupported_response"
    if str(payload.get("kind", "")).lower() == "native_transfer":
        return "prepare_native_transaction"
    return "unsupported_response"


def route_after_native_prepare(state: MercuryState) -> str:
    """Route preparation errors away from the transaction pipeline."""

    if state.get("error"):
        return "reject_transaction"
    return "resolve_nonce"


def _native_payload_from_state(state: MercuryState) -> dict[str, Any]:
    raw = state.get("parsed_intent", state.get("raw_input"))
    if isinstance(raw, dict):
        intent = raw.get("intent")
        return intent if isinstance(intent, dict) else raw
    raise ValueError("Native graph requires a structured transaction intent.")
