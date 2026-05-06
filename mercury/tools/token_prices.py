"""Read-only LangChain tool for Alchemy token prices by contract address."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from mercury.alchemy.prices import AlchemyPricesClientProtocol, AlchemyTokenPricesClient
from mercury.custody.oneclaw import SecretStore
from mercury.tools.schemas import TokenPricesToolInput


@dataclass(frozen=True)
class AlchemyPricesToolDeps:
    """Injectable deps for ``get_token_prices`` (1Claw path only, never the raw API key)."""

    secret_store: SecretStore
    api_key_secret_path: str


def get_token_prices(
    *,
    tokens: list[dict[str, Any]],
    client: AlchemyPricesClientProtocol,
) -> dict[str, Any]:
    """Resolve current token prices for checksummed addresses on supported chains."""

    parsed = TokenPricesToolInput.model_validate({"tokens": tokens})
    entries = [(e.chain, e.token_address) for e in parsed.tokens]
    return client.fetch_prices_by_address(entries)


def create_alchemy_token_prices_tool(
    deps: AlchemyPricesToolDeps,
    *,
    client: AlchemyPricesClientProtocol | None = None,
) -> BaseTool:
    """Build a fakeable ``get_token_prices`` tool."""

    resolved: AlchemyPricesClientProtocol = client or AlchemyTokenPricesClient(
        secret_store=deps.secret_store,
        api_key_secret_path=deps.api_key_secret_path,
    )

    def get_token_prices_tool(tokens: list[dict[str, Any]]) -> dict[str, Any]:
        return get_token_prices(tokens=tokens, client=resolved)

    return StructuredTool.from_function(
        func=get_token_prices_tool,
        name="get_token_prices",
        description=(
            "Fetch current token prices from Alchemy Prices API by (chain, token contract). "
            "Supports up to 25 entries and 3 distinct networks (ethereum, base, arbitrum, optimism)."
        ),
    )