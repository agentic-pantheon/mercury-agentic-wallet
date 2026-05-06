"""Typed schemas for Mercury read-only tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mercury.alchemy.transfers import ALLOWED_TRANSFER_CATEGORIES, TRANSFER_DEFAULT_CATEGORIES
from mercury.models.addresses import normalize_evm_address

PORTFOLIO_MAX_CHAINS_PER_REQUEST = 5
TRANSFER_HISTORY_MAX_COUNT = 1000


class ChainInput(BaseModel):
    """Base input requiring an explicit chain name."""

    model_config = ConfigDict(frozen=True)

    chain: str = Field(min_length=1, description="Supported chain name, such as ethereum or base.")

    @field_validator("chain")
    @classmethod
    def normalize_chain(cls, value: str) -> str:
        return value.strip().lower()


class NativeBalanceInput(ChainInput):
    """Input for native balance reads."""

    wallet_address: str = Field(min_length=1)

    @field_validator("wallet_address")
    @classmethod
    def validate_wallet_address(cls, value: str) -> str:
        return normalize_evm_address(value)


class ERC20MetadataInput(ChainInput):
    """Input for ERC20 metadata reads."""

    token_address: str = Field(min_length=1)

    @field_validator("token_address")
    @classmethod
    def validate_token_address(cls, value: str) -> str:
        return normalize_evm_address(value)


class ERC20BalanceInput(ERC20MetadataInput):
    """Input for ERC20 balance reads."""

    wallet_address: str = Field(min_length=1)

    @field_validator("wallet_address")
    @classmethod
    def validate_wallet_address(cls, value: str) -> str:
        return normalize_evm_address(value)


class ERC20AllowanceInput(ERC20MetadataInput):
    """Input for ERC20 allowance reads."""

    owner_address: str = Field(min_length=1)
    spender_address: str = Field(min_length=1)

    @field_validator("owner_address", "spender_address")
    @classmethod
    def validate_allowance_address(cls, value: str) -> str:
        return normalize_evm_address(value)


class ContractReadInput(ChainInput):
    """Input for generic read-only contract calls."""

    contract_address: str = Field(min_length=1)
    abi_fragment: list[dict[str, Any]] = Field(min_length=1)
    function_name: str = Field(min_length=1)
    args: list[Any] = Field(default_factory=list)

    @field_validator("contract_address")
    @classmethod
    def validate_contract_address(cls, value: str) -> str:
        return normalize_evm_address(value)


class TokenPriceRequestItem(BaseModel):
    """One `(chain, token_address)` pair for Alchemy token price lookup."""

    model_config = ConfigDict(frozen=True)

    chain: str = Field(min_length=1)
    token_address: str = Field(min_length=1)

    @field_validator("chain")
    @classmethod
    def normalize_chain(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("chain must not be empty.")
        return normalized

    @field_validator("token_address")
    @classmethod
    def normalize_token_address(cls, value: str) -> str:
        return normalize_evm_address(value)


class TokenPricesToolInput(BaseModel):
    """Batch input for ``get_token_prices``."""

    model_config = ConfigDict(frozen=True)

    tokens: list[TokenPriceRequestItem] = Field(min_length=1)

    # Alchemy Prices: max 25 entries, max 3 distinct networks.
    @field_validator("tokens")
    @classmethod
    def validate_batch_limits(
        cls,
        value: list[TokenPriceRequestItem],
    ) -> list[TokenPriceRequestItem]:
        if len(value) > 25:
            msg = "token_prices supports at most 25 token entries per request."
            raise ValueError(msg)
        distinct_chains = {item.chain for item in value}
        if len(distinct_chains) > 3:
            msg = "token_prices supports at most 3 distinct chains per request."
            raise ValueError(msg)
        return value


class PortfolioTokensToolInput(BaseModel):
    """Input for ``get_portfolio_tokens`` (one wallet, up to five Mercury chains)."""

    model_config = ConfigDict(frozen=True)

    wallet_address: str = Field(min_length=1)
    chains: list[str] = Field(min_length=1, max_length=PORTFOLIO_MAX_CHAINS_PER_REQUEST)
    with_metadata: bool = True
    with_prices: bool = True
    include_native_tokens: bool = True
    include_erc20_tokens: bool = True
    page_key: str | None = None

    @field_validator("wallet_address")
    @classmethod
    def normalize_wallet(cls, value: str) -> str:
        return normalize_evm_address(value)

    @field_validator("chains")
    @classmethod
    def normalize_chains(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in value:
            c = raw.strip().lower()
            if not c or c in seen:
                continue
            out.append(c)
            seen.add(c)
        if not out:
            raise ValueError("chains must include at least one non-empty chain name.")
        if len(out) > PORTFOLIO_MAX_CHAINS_PER_REQUEST:
            msg = (
                f"portfolio_tokens supports at most {PORTFOLIO_MAX_CHAINS_PER_REQUEST} "
                "distinct chains per request."
            )
            raise ValueError(msg)
        return out

    @field_validator("page_key")
    @classmethod
    def normalize_page_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class TransferHistoryToolInput(BaseModel):
    """Input for ``get_transfer_history`` (Alchemy ``alchemy_getAssetTransfers``)."""

    model_config = ConfigDict(frozen=True)

    chain: str = Field(min_length=1)
    wallet_address: str = Field(min_length=1)
    direction: str = "both"
    categories: list[str] | None = None
    from_block: str | int | None = None
    to_block: str | int | None = None
    max_count: int = Field(default=100, ge=1, le=TRANSFER_HISTORY_MAX_COUNT)
    page_key: str | None = None
    with_metadata: bool = True
    exclude_zero_value: bool = True

    @field_validator("chain")
    @classmethod
    def normalize_chain_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("chain must not be empty.")
        return normalized

    @field_validator("wallet_address")
    @classmethod
    def normalize_wallet(cls, value: str) -> str:
        return normalize_evm_address(value)

    @field_validator("page_key")
    @classmethod
    def normalize_page_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _normalize_direction_categories_page(self) -> "TransferHistoryToolInput":
        aliases = {
            "in": "incoming",
            "incoming": "incoming",
            "out": "outgoing",
            "outgoing": "outgoing",
            "both": "both",
        }
        key = self.direction.strip().lower()
        if key not in aliases:
            msg = "direction must be incoming, outgoing, or both."
            raise ValueError(msg)
        dir_norm = aliases[key]

        cats = self.categories
        if not cats:
            resolved = list(TRANSFER_DEFAULT_CATEGORIES)
        else:
            resolved = [c.strip().lower() for c in cats if isinstance(c, str) and c.strip()]
            if not resolved:
                raise ValueError("categories must include at least one non-empty category name.")
            for c in resolved:
                if c not in ALLOWED_TRANSFER_CATEGORIES:
                    allowed = ", ".join(sorted(ALLOWED_TRANSFER_CATEGORIES))
                    raise ValueError(f"Unsupported transfer category {c!r}. Allowed: {allowed}.")

        page_key = None if dir_norm == "both" else self.page_key

        return self.model_copy(
            update={
                "direction": dir_norm,
                "categories": resolved,
                "page_key": page_key,
            }
        )


class NativeBalanceOutput(BaseModel):
    """Native balance read result."""

    model_config = ConfigDict(frozen=True)

    chain: str
    chain_id: int
    wallet_address: str
    raw_wei: int
    formatted: str
    symbol: str


class ERC20MetadataOutput(BaseModel):
    """ERC20 metadata read result."""

    model_config = ConfigDict(frozen=True)

    chain: str
    chain_id: int
    token_address: str
    decimals: int
    symbol: str | None = None
    name: str | None = None


class ERC20BalanceOutput(BaseModel):
    """ERC20 balance read result."""

    model_config = ConfigDict(frozen=True)

    chain: str
    chain_id: int
    token_address: str
    wallet_address: str
    raw_amount: int
    formatted: str
    decimals: int
    symbol: str | None = None
    name: str | None = None


class ERC20AllowanceOutput(BaseModel):
    """ERC20 allowance read result."""

    model_config = ConfigDict(frozen=True)

    chain: str
    chain_id: int
    token_address: str
    owner_address: str
    spender_address: str
    raw_amount: int
    formatted: str
    decimals: int
    symbol: str | None = None
    name: str | None = None


class ContractReadOutput(BaseModel):
    """Generic read-only contract call result."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    chain: str
    chain_id: int
    contract_address: str
    function_name: str
    result: Any
