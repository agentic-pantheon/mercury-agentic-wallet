"""Normalized swap intent, quote, route, provider, and execution models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mercury.models.addresses import normalize_evm_address
from mercury.models.erc20 import ZERO_ADDRESS
from mercury.models.native_tokens import normalize_swap_input_from_token
from mercury.models.transactions import HexData

BasisPoints = Annotated[int, Field(ge=0, le=10_000)]


class SwapProviderName(StrEnum):
    """Swap providers supported behind Mercury's normalized interface."""

    LIFI = "lifi"
    COWSWAP = "cowswap"
    UNISWAP = "uniswap"


class SwapRouteKind(StrEnum):
    """Route categories that carry different policy requirements."""

    SWAP = "swap"
    BRIDGE = "bridge"


class SwapExecutionType(StrEnum):
    """Normalized execution payload types returned by providers."""

    EVM_TRANSACTION = "evm_transaction"
    EIP712_ORDER = "eip712_order"
    UNSUPPORTED = "unsupported"


class SwapIntent(BaseModel):
    """User intent to swap tokens; *from_token* may use a native sentinel."""

    model_config = ConfigDict(frozen=True)

    wallet_id: str = Field(min_length=1)
    chain: str = Field(min_length=1)
    from_token: str = Field(min_length=1)
    to_token: str = Field(min_length=1)
    amount_in: str = Field(min_length=1)
    min_amount_out: str | None = Field(default=None, min_length=1)
    max_slippage_bps: BasisPoints | None = None
    provider_preference: SwapProviderName | None = None
    recipient_address: str | None = Field(default=None, min_length=1)
    idempotency_key: str = Field(min_length=1)
    to_chain: str | None = Field(
        default=None,
        min_length=1,
        description="Optional destination chain; with to_chain_id enables bridge swaps.",
    )
    to_chain_id: int | None = Field(
        default=None,
        gt=0,
        description="Optional destination chain id; differs from source for bridge quotes.",
    )

    @field_validator("chain", "to_chain")
    @classmethod
    def normalize_intent_chain_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower()

    @field_validator("from_token")
    @classmethod
    def normalize_from_token(cls, value: str) -> str:
        return normalize_swap_input_from_token(value)

    @field_validator("to_token", "recipient_address")
    @classmethod
    def normalize_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_evm_address(value)

    @model_validator(mode="after")
    def validate_tokens(self) -> SwapIntent:
        if self.from_token == self.to_token:
            raise ValueError("Swap tokens must be different.")
        return self

    @model_validator(mode="after")
    def validate_bridge_fields(self) -> SwapIntent:
        if self.to_chain is not None and self.to_chain_id is not None:
            from mercury.chains import get_chain_by_name

            chain = get_chain_by_name(self.to_chain)
            if chain.chain_id != self.to_chain_id:
                raise ValueError("to_chain and to_chain_id must refer to the same network.")
        return self


class SwapQuoteRequest(BaseModel):
    """Provider quote input after chain and wallet resolution."""

    model_config = ConfigDict(frozen=True)

    wallet_id: str = Field(min_length=1)
    wallet_address: str = Field(min_length=1)
    chain: str = Field(min_length=1)
    chain_id: int = Field(gt=0)
    from_token: str = Field(min_length=1)
    to_token: str = Field(min_length=1)
    amount_in: str = Field(min_length=1)
    amount_in_raw: int = Field(gt=0)
    max_slippage_bps: BasisPoints | None = None
    min_amount_out: str | None = Field(default=None, min_length=1)
    recipient_address: str | None = Field(default=None, min_length=1)
    idempotency_key: str = Field(min_length=1)
    to_chain: str | None = Field(
        default=None,
        min_length=1,
        description="Optional destination chain name; must match to_chain_id when both are set.",
    )
    to_chain_id: int | None = Field(
        default=None,
        gt=0,
        description="None means same-chain; otherwise the destination chain id for a bridge quote.",
    )
    from_token_is_native: bool = Field(
        default=False,
        description=(
            "True when selling the chain native token; from_token is the zero placeholder."
        ),
    )
    wrapped_from_token: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Wrapped native token when from_token_is_native; providers use this for routing APIs."
        ),
    )

    @field_validator("chain", "to_chain")
    @classmethod
    def normalize_request_chain_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower()

    @field_validator("from_token")
    @classmethod
    def normalize_from_token(cls, value: str) -> str:
        return normalize_swap_input_from_token(value)

    @field_validator("wrapped_from_token")
    @classmethod
    def normalize_wrapped_from_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_evm_address(value)

    @field_validator("wallet_address", "to_token", "recipient_address")
    @classmethod
    def normalize_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_evm_address(value)

    @property
    def effective_recipient(self) -> str:
        """Return the recipient address used by the provider request."""

        return self.recipient_address or self.wallet_address

    @model_validator(mode="after")
    def validate_request_destination(self) -> SwapQuoteRequest:
        if self.to_chain is not None and self.to_chain_id is not None:
            from mercury.chains import get_chain_by_name

            chain = get_chain_by_name(self.to_chain)
            if chain.chain_id != self.to_chain_id:
                raise ValueError("to_chain and to_chain_id must refer to the same network.")
        return self

    @model_validator(mode="after")
    def validate_native_sell_fields(self) -> SwapQuoteRequest:
        canonical_native = normalize_evm_address(ZERO_ADDRESS)
        if self.from_token == canonical_native:
            if not self.from_token_is_native:
                raise ValueError(
                    "from_token_is_native must be True when from_token is the "
                    "canonical native placeholder."
                )
            if self.wrapped_from_token is None:
                raise ValueError("wrapped_from_token is required when selling native currency.")
        else:
            if self.from_token_is_native:
                raise ValueError("from_token_is_native cannot be True for ERC-20 sell tokens.")
            if self.wrapped_from_token is not None:
                raise ValueError("wrapped_from_token must be omitted for ERC-20 sell tokens.")
        return self


class SwapRoute(BaseModel):
    """Normalized provider route details used by policy and UX."""

    model_config = ConfigDict(frozen=True)

    provider: SwapProviderName
    route_id: str = Field(min_length=1)
    route_kind: SwapRouteKind = SwapRouteKind.SWAP
    from_chain_id: int = Field(gt=0)
    to_chain_id: int = Field(gt=0)
    from_token: str = Field(min_length=1)
    to_token: str = Field(min_length=1)
    spender_address: str | None = Field(default=None, min_length=1)
    steps: tuple[str, ...] = ()

    @field_validator("from_token", "to_token", "spender_address")
    @classmethod
    def normalize_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_evm_address(value)


class SwapQuote(BaseModel):
    """Validated quote returned by a normalized swap provider."""

    model_config = ConfigDict(frozen=True)

    provider: SwapProviderName
    request: SwapQuoteRequest
    route: SwapRoute
    amount_in_raw: int = Field(gt=0)
    expected_amount_out_raw: int = Field(gt=0)
    min_amount_out_raw: int | None = Field(default=None, ge=0)
    slippage_bps: BasisPoints | None = None
    expires_at: datetime | None = None
    recipient_address: str = Field(min_length=1)
    raw_quote: dict[str, Any] = Field(default_factory=dict)

    @field_validator("recipient_address")
    @classmethod
    def normalize_recipient(cls, value: str) -> str:
        return normalize_evm_address(value)

    @field_validator("expires_at")
    @classmethod
    def normalize_expiry(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_quote_consistency(self) -> SwapQuote:
        if self.provider != self.route.provider:
            raise ValueError("Quote provider must match route provider.")
        if self.amount_in_raw != self.request.amount_in_raw:
            raise ValueError("Quote amount_in_raw must match the request amount.")
        if self.route.from_chain_id != self.request.chain_id:
            raise ValueError("Route source chain must match the request chain.")
        if self.request.to_chain_id is None:
            if self.route.to_chain_id != self.request.chain_id:
                raise ValueError(
                    "Route destination chain must match the request chain for same-chain quotes."
                )
        elif self.route.to_chain_id != self.request.to_chain_id:
            raise ValueError("Route destination chain must match the request destination chain.")
        if self.route.from_token != self.request.from_token:
            raise ValueError("Route source token must match the request token.")
        if self.route.to_token != self.request.to_token:
            raise ValueError("Route destination token must match the request token.")
        if self.recipient_address != self.request.effective_recipient:
            raise ValueError("Quote recipient must match the request recipient.")
        return self


class SwapEVMTransaction(BaseModel):
    """Provider-built EVM transaction payload before Mercury gas/nonce population."""

    model_config = ConfigDict(frozen=True)

    chain_id: int = Field(gt=0)
    to: str = Field(min_length=1)
    data: HexData
    value_wei: int = Field(default=0, ge=0)

    @field_validator("to")
    @classmethod
    def normalize_to(cls, value: str) -> str:
        return normalize_evm_address(value)


class SwapTypedOrder(BaseModel):
    """Normalized typed-data order payload for order-book style providers."""

    model_config = ConfigDict(frozen=True)

    chain_id: int = Field(gt=0)
    typed_data: dict[str, Any] = Field(min_length=1)
    submit_url: str | None = Field(default=None, min_length=1)


class SwapExecution(BaseModel):
    """Normalized provider execution result."""

    model_config = ConfigDict(frozen=True)

    provider: SwapProviderName
    execution_type: SwapExecutionType
    quote: SwapQuote
    transaction: SwapEVMTransaction | None = None
    order: SwapTypedOrder | None = None
    unsupported_reason: str | None = Field(default=None, min_length=1)
    raw_execution: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_execution_payload(self) -> SwapExecution:
        if self.provider != self.quote.provider:
            raise ValueError("Execution provider must match quote provider.")
        if self.execution_type == SwapExecutionType.EVM_TRANSACTION:
            if self.transaction is None:
                raise ValueError("EVM swap execution requires a transaction.")
            if self.transaction.chain_id != self.quote.request.chain_id:
                raise ValueError("Swap transaction chain_id must match the quote chain.")
        elif self.execution_type == SwapExecutionType.EIP712_ORDER:
            if self.order is None:
                raise ValueError("Typed order swap execution requires an order payload.")
            if self.order.chain_id != self.quote.request.chain_id:
                raise ValueError("Swap order chain_id must match the quote chain.")
        elif self.execution_type == SwapExecutionType.UNSUPPORTED:
            if not self.unsupported_reason:
                raise ValueError("Unsupported swap execution requires a reason.")
        return self
