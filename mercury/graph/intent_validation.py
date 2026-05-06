"""Upfront validation and normalization for Mercury invoke intents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from pydantic import ValidationError

from mercury.graph.intents import (
    _KIND_ALIASES,
    UnsupportedIntent,
    UnsupportedIntentError,
    parse_readonly_intent,
)
from mercury.graph.state import MercuryState
from mercury.known_addresses.book import KnownAddressMissingError, lookup_address
from mercury.models.erc20 import ERC20ApprovalIntent, ERC20TransferIntent
from mercury.models.errors import (
    MercuryErrorInfo,
    normalize_exception,
    unsupported_intent,
    validation_failed,
    validation_failed_from_pydantic,
)
from mercury.models.native_tx import NativeTransferIntent
from mercury.models.swaps import SwapIntent

_STAGE = "validate_invoke_intent"

_VALUE_MOVING_KINDS: frozenset[str] = frozenset(
    {"erc20_transfer", "erc20_approval", "native_transfer", "swap"}
)

_VALUE_MOVING_MODEL: dict[str, type[Any]] = {
    "erc20_transfer": ERC20TransferIntent,
    "erc20_approval": ERC20ApprovalIntent,
    "native_transfer": NativeTransferIntent,
    "swap": SwapIntent,
}


def unwrap_intent_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Return nested ``intent`` dict when present; otherwise ``raw``.

    Matches :func:`mercury.graph.intents._unwrap_payload` semantics so routing
    agrees with :func:`mercury.graph.intents.parse_readonly_intent`.
    """

    intent = raw.get("intent")
    if isinstance(intent, dict):
        return intent
    return raw


def merge_canonical_into_raw_input(
    raw_input: dict[str, Any],
    canonical: dict[str, Any],
) -> dict[str, Any]:
    """Merge validated intent fields into ``raw_input``, preserving boundary-only keys."""

    out = dict(raw_input)
    if isinstance(raw_input.get("intent"), dict):
        out["intent"] = dict(canonical)
    else:
        out.update(canonical)
    return out


def merged_invoke_validation_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Nested ``intent`` plus top-level boundary fields (chain, wallet_id, metadata, etc.).

    Aligns with the merged dict shape produced by
    :func:`mercury.service.api._intent_with_boundary_fields` while still accepting
    a nested ``intent`` wrapper from other callers.
    """

    inner = unwrap_intent_payload(raw)
    merged = dict(inner)
    if inner is not raw:
        for key in ("wallet_id", "chain", "idempotency_key"):
            if key in raw:
                merged.setdefault(key, raw[key])
        if "metadata" in raw:
            merged.setdefault("metadata", raw["metadata"])
    return merged


def validate_invoke_intent(
    state: MercuryState | Mapping[str, Any],
) -> tuple[MercuryState | None, MercuryErrorInfo | None]:
    """Validate invoke intent input and normalize ``raw_input``.

    Parameters
    ----------
    state:
        Either a :class:`~mercury.graph.state.MercuryState` mapping (uses
        ``raw_input``) or the merged intent dict itself (same surface as
        :func:`mercury.service.api._intent_with_boundary_fields`).

    Returns
    -------
    tuple
        ``(updated_state, None)`` on success with canonical intent fields merged
        back into ``raw_input``, or ``(None, error)`` with structured
        :class:`~mercury.models.errors.MercuryErrorInfo`.
    """

    raw = _extract_raw_blob(state)

    if isinstance(raw, str):
        return None, unsupported_intent(
            message=(
                "String intents cannot be validated for invoke; "
                "provide a structured JSON object with a ``kind`` field."
            ),
            stage=_STAGE,
            details={"remediation": "Replace text intents with a structured intent payload."},
        )

    if not isinstance(raw, dict):
        return None, validation_failed(
            message="Invoke intent must be a JSON object.",
            stage=_STAGE,
        )

    kind_payload = unwrap_intent_payload(raw)
    raw_kind = kind_payload.get("kind", kind_payload.get("type", kind_payload.get("intent")))
    if not isinstance(raw_kind, str):
        return None, validation_failed(
            message="Structured intents must include a string ``kind`` field.",
            stage=_STAGE,
        )

    kind_lower = raw_kind.strip().lower()
    merged = merged_invoke_validation_payload(raw)

    if kind_lower in _VALUE_MOVING_KINDS:
        model_cls = _VALUE_MOVING_MODEL[kind_lower]
        try:
            validated = model_cls.model_validate(merged)
        except ValidationError as exc:
            return None, _validation_failed_from_pydantic_invoke(exc, merged)

        canonical = validated.model_dump(mode="json")
        new_raw = merge_canonical_into_raw_input(raw, canonical)
        return _success_state(state, new_raw), None

    if kind_lower in _KIND_ALIASES:
        try:
            parsed = parse_readonly_intent(merged, None)
        except UnsupportedIntentError as exc:
            info_err = normalize_exception(exc, stage=_STAGE)
            return None, _enrich_invoke_validation_error(info_err, merged)

        if isinstance(parsed, UnsupportedIntent):
            info_u = unsupported_intent(message=parsed.reason, stage=_STAGE)
            return None, _enrich_invoke_validation_error(info_u, merged)

        canonical = parsed.model_dump(mode="json")
        new_raw = merge_canonical_into_raw_input(raw, canonical)
        return _success_state(state, new_raw), None

    return None, unsupported_intent(
        message=f"Unsupported wallet intent: {raw_kind}.",
        stage=_STAGE,
    )


def _extract_raw_blob(state: MercuryState | Mapping[str, Any]) -> Any:
    if isinstance(state, Mapping) and "raw_input" in state:
        return state["raw_input"]
    return state


def _validation_failed_from_pydantic_invoke(
    exc: ValidationError, merged: dict[str, Any]
) -> MercuryErrorInfo:
    info = validation_failed_from_pydantic(exc, stage=_STAGE)
    remediation = (
        "Compare your payload with the required fields for this intent kind "
        "(addresses, amounts, chain, wallet_id, and idempotency_key where applicable)."
    )
    info = info.model_copy(update={"details": {**info.details, "remediation": remediation}})
    return _enrich_invoke_validation_error(info, merged)


def _success_state(
    state: MercuryState | Mapping[str, Any],
    merged_raw: dict[str, Any],
) -> MercuryState:
    if isinstance(state, Mapping) and "raw_input" in state:
        out = dict(cast(dict[str, Any], state))
        out["raw_input"] = merged_raw
        return cast(MercuryState, out)
    return cast(MercuryState, {"raw_input": merged_raw})


def _enrich_invoke_validation_error(
    info: MercuryErrorInfo,
    merged: dict[str, Any],
) -> MercuryErrorInfo:
    """Attach orchestrator-facing remediation without changing MercuryError semantics."""

    details = dict(info.details)
    extras: dict[str, Any] = {}

    merged_kind = str(merged.get("kind", "") or "").strip().lower()

    abi_value = merged.get("abi_fragment")
    if merged_kind == "contract_read" and isinstance(abi_value, str):
        extras["abi_fragment_expected"] = "list[dict] (JSON ABI entries)"
        extras["abi_fragment_example_balanceOf"] = [
            {
                "type": "function",
                "name": "balanceOf",
                "stateMutability": "view",
                "inputs": [{"name": "account", "type": "address"}],
                "outputs": [{"type": "uint256"}],
            }
        ]
        extras["alternate_intent_suggestion"] = (
            "For ERC-20 balances use ``kind: erc20_balance`` with ``token_address`` "
            "and ``wallet_address``."
        )

    pyd_errors = (
        errors
        if isinstance((errors := details.get("errors")), list)
        else None
    )
    if pyd_errors:
        suggestion = _known_address_resolution_suggestion(pyd_errors, merged)
        if suggestion is not None:
            extras["known_address_catalog_suggestion"] = suggestion

    if "known_address_catalog_suggestion" not in extras:
        fallback = _known_address_resolution_from_merged_fields(merged)
        if fallback is not None:
            extras["known_address_catalog_suggestion"] = fallback

    if not extras:
        return info
    merged_details = dict(details)
    merged_details.update(extras)
    return info.model_copy(update={"details": merged_details})


def _looks_like_plain_symbol(raw: Any) -> bool:
    """Heuristic ticker / symbol-ish token that is not hex."""

    if not isinstance(raw, str):
        return False
    trimmed = raw.strip()
    if not trimmed.isascii():
        return False
    if trimmed.lower().startswith("0x"):
        return False
    if trimmed.isdigit():
        return False
    letters = sum(1 for c in trimmed if c.isalpha())
    if letters < 2:
        return False
    allowed = trimmed.replace("_", "")
    return allowed.isalnum() and 3 <= len(allowed) <= 16


_KNOWN_ADDRESS_TOKEN_FIELDS = (
    "token_address",
    "from_token",
    "to_token",
)


def _known_address_resolution_from_merged_fields(
    merged: dict[str, Any],
) -> dict[str, Any] | None:
    chain = merged.get("chain")
    if not isinstance(chain, str) or not chain.strip():
        return None
    kind = str(merged.get("kind", "")).strip().lower()

    synthetic: list[dict[str, Any]] = []
    for field in _KNOWN_ADDRESS_TOKEN_FIELDS:
        if kind == "erc20_balance" and field != "token_address":
            continue
        inp = merged.get(field)
        if not _looks_like_plain_symbol(inp):
            continue
        synthetic.append({"loc": (field,), "input": inp})
    if not synthetic:
        return None
    return _known_address_resolution_suggestion(synthetic, merged)


def _known_address_resolution_suggestion(
    errors: list[Any],
    merged: dict[str, Any],
) -> dict[str, Any] | None:
    chain = merged.get("chain")
    if not isinstance(chain, str) or not chain.strip():
        return None

    for entry in errors:
        if not isinstance(entry, Mapping):
            continue
        parts = tuple(entry.get("loc", ()))
        if not parts:
            continue
        field = str(parts[-1])
        if field not in _KNOWN_ADDRESS_TOKEN_FIELDS:
            continue
        inp = entry.get("input")
        if not _looks_like_plain_symbol(inp):
            continue
        try:
            address = lookup_address(chain, "token", str(inp).strip())
        except KnownAddressMissingError:
            return {
                "field": field,
                "provided": str(inp).strip(),
                "chain": chain.strip().lower(),
                "message": (
                    "`token_address`/token fields expect a checksummed 0x address. "
                    "If ``{provided}`` is a ticker symbol, resolve it with ``kind: known_address`` "
                    "(``category``: ``token``, ``key``: ``…``)."
                ).format(**{"provided": str(inp).strip()}),
            }

        suggestion: dict[str, Any] = {
            "field": field,
            "provided": str(inp).strip(),
            "resolved_checksum_address": address,
            "chain": chain.strip().lower(),
            "message": (
                f"Interpreted `{inp}` as a token symbol → checksum address `{address}` on-catalog. "
                f"Replace `{field}` with that `0x` address "
                "(or resolve via ``kind: known_address``)."
            ),
        }
        if field.startswith("token") or field in {"from_token", "to_token"}:
            suggestion["intent_kind_tip"] = (
                "erc20_balance for balances; swaps still require hex `token` addresses."
            )
        return suggestion

    return None


__all__ = [
    "merge_canonical_into_raw_input",
    "merged_invoke_validation_payload",
    "unwrap_intent_payload",
    "validate_invoke_intent",
]
