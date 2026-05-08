"""Read-only ERC20 tools."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from mercury.abi import ERC20_READ_ABI
from mercury.models.addresses import normalize_evm_address
from mercury.models.amounts import format_units, validate_token_decimals
from mercury.tools.evm import ProviderFactoryLike, _call_contract_function
from mercury.tools.schemas import (
    ERC20AllowanceInput,
    ERC20AllowanceOutput,
    ERC20BalanceInput,
    ERC20BalanceOutput,
    ERC20MetadataInput,
    ERC20MetadataOutput,
)


def get_erc20_metadata(
    *,
    chain: str,
    token_address: str,
    provider_factory: ProviderFactoryLike,
) -> ERC20MetadataOutput:
    """Read ERC20 decimals, symbol, and name."""

    normalized_token = normalize_evm_address(token_address)
    provider = provider_factory.create(chain)
    contract = provider.client.eth.contract(address=normalized_token, abi=ERC20_READ_ABI)
    decimals = validate_token_decimals(int(_call_contract_function(contract, "decimals", [])))

    return ERC20MetadataOutput(
        chain=provider.chain.name,
        chain_id=provider.chain.chain_id,
        token_address=normalized_token,
        decimals=decimals,
        symbol=_optional_text_call(contract, "symbol"),
        name=_optional_text_call(contract, "name"),
    )


def get_erc20_balance(
    *,
    chain: str,
    token_address: str,
    wallet_address: str,
    provider_factory: ProviderFactoryLike,
) -> ERC20BalanceOutput:
    """Read an ERC20 token balance."""

    normalized_token = normalize_evm_address(token_address)
    normalized_wallet = normalize_evm_address(wallet_address)
    provider = provider_factory.create(chain)
    contract = provider.client.eth.contract(address=normalized_token, abi=ERC20_READ_ABI)
    decimals = validate_token_decimals(int(_call_contract_function(contract, "decimals", [])))
    raw_amount = int(_call_contract_function(contract, "balanceOf", [normalized_wallet]))

    return ERC20BalanceOutput(
        chain=provider.chain.name,
        chain_id=provider.chain.chain_id,
        token_address=normalized_token,
        wallet_address=normalized_wallet,
        raw_amount=raw_amount,
        formatted=format_units(raw_amount, decimals),
        decimals=decimals,
        symbol=_optional_text_call(contract, "symbol"),
        name=_optional_text_call(contract, "name"),
    )


def get_erc20_allowance(
    *,
    chain: str,
    token_address: str,
    owner_address: str,
    spender_address: str,
    provider_factory: ProviderFactoryLike,
) -> ERC20AllowanceOutput:
    """Read an ERC20 allowance."""

    normalized_token = normalize_evm_address(token_address)
    normalized_owner = normalize_evm_address(owner_address)
    normalized_spender = normalize_evm_address(spender_address)
    provider = provider_factory.create(chain)
    contract = provider.client.eth.contract(address=normalized_token, abi=ERC20_READ_ABI)
    decimals = validate_token_decimals(int(_call_contract_function(contract, "decimals", [])))
    raw_amount = int(
        _call_contract_function(contract, "allowance", [normalized_owner, normalized_spender])
    )

    return ERC20AllowanceOutput(
        chain=provider.chain.name,
        chain_id=provider.chain.chain_id,
        token_address=normalized_token,
        owner_address=normalized_owner,
        spender_address=normalized_spender,
        raw_amount=raw_amount,
        formatted=format_units(raw_amount, decimals),
        decimals=decimals,
        symbol=_optional_text_call(contract, "symbol"),
        name=_optional_text_call(contract, "name"),
    )


def create_erc20_tools(provider_factory: ProviderFactoryLike) -> list[BaseTool]:
    """Create LangChain-compatible ERC20 read tools bound to a provider factory."""

    def metadata_tool(chain: str, token_address: str) -> dict[str, Any]:
        return get_erc20_metadata(
            chain=chain,
            token_address=token_address,
            provider_factory=provider_factory,
        ).model_dump()

    def balance_tool(chain: str, token_address: str, wallet_address: str) -> dict[str, Any]:
        return get_erc20_balance(
            chain=chain,
            token_address=token_address,
            wallet_address=wallet_address,
            provider_factory=provider_factory,
        ).model_dump()

    def allowance_tool(
        chain: str,
        token_address: str,
        owner_address: str,
        spender_address: str,
    ) -> dict[str, Any]:
        return get_erc20_allowance(
            chain=chain,
            token_address=token_address,
            owner_address=owner_address,
            spender_address=spender_address,
            provider_factory=provider_factory,
        ).model_dump()

    return [
        StructuredTool.from_function(
            func=metadata_tool,
            name="get_erc20_metadata",
            description="Read ERC20 decimals, symbol, and name.",
            args_schema=ERC20MetadataInput,
        ),
        StructuredTool.from_function(
            func=balance_tool,
            name="get_erc20_balance",
            description="Read an ERC20 balance for a wallet address.",
            args_schema=ERC20BalanceInput,
        ),
        StructuredTool.from_function(
            func=allowance_tool,
            name="get_erc20_allowance",
            description="Read an ERC20 allowance for an owner and spender.",
            args_schema=ERC20AllowanceInput,
        ),
    ]


def _optional_text_call(contract: Any, function_name: str) -> str | None:
    """Decode ``name()`` / ``symbol()`` when present; any RPC/ABI mismatch returns ``None``."""

    try:
        value = _call_contract_function(contract, function_name, [])
    except Exception:
        return None

    if isinstance(value, bytes):
        return value.rstrip(b"\x00").decode("utf-8")
    if isinstance(value, str):
        return value
    return str(value)
