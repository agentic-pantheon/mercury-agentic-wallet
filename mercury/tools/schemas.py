"""Typed schemas for Mercury read-only tools."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mercury.models.addresses import normalize_evm_address


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
    def validate_batch_limits(cls, value: list[TokenPriceRequestItem]) -> list[TokenPriceRequestItem]:
        if len(value) > 25:
            msg = "token_prices supports at most 25 token entries per request."
            raise ValueError(msg)
        distinct_chains = {item.chain for item in value}
        if len(distinct_chains) > 3:
            msg = "token_prices supports at most 3 distinct chains per request."
            raise ValueError(msg)
        return value


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
