"""Read-only LangChain tool for Alchemy Portfolio tokens-by-wallet API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from mercury.alchemy.portfolio import AlchemyPortfolioClient, AlchemyPortfolioClientProtocol
from mercury.custody.oneclaw import SecretStore
from mercury.tools.schemas import PortfolioTokensToolInput


@dataclass(frozen=True)
class AlchemyPortfolioToolDeps:
    """Injectable deps for ``get_portfolio_tokens`` (1Claw path only, never the raw API key)."""

    secret_store: SecretStore
    api_key_secret_path: str


def get_portfolio_tokens(
    *,
    wallet_address: str,
    chains: list[str],
    with_metadata: bool = True,
    with_prices: bool = True,
    include_native_tokens: bool = True,
    include_erc20_tokens: bool = True,
    page_key: str | None = None,
    client: AlchemyPortfolioClientProtocol,
) -> dict[str, Any]:
    """Fetch normalized fungible token rows for one wallet across up to five networks."""

    parsed = PortfolioTokensToolInput.model_validate(
        {
            "wallet_address": wallet_address,
            "chains": chains,
            "with_metadata": with_metadata,
            "with_prices": with_prices,
            "include_native_tokens": include_native_tokens,
            "include_erc20_tokens": include_erc20_tokens,
            "page_key": page_key,
        }
    )
    return client.fetch_tokens_for_wallet(
        wallet_address=parsed.wallet_address,
        mercury_chains=parsed.chains,
        with_metadata=parsed.with_metadata,
        with_prices=parsed.with_prices,
        include_native_tokens=parsed.include_native_tokens,
        include_erc20_tokens=parsed.include_erc20_tokens,
        page_key=parsed.page_key,
    )


def create_alchemy_portfolio_tokens_tool(
    deps: AlchemyPortfolioToolDeps,
    *,
    client: AlchemyPortfolioClientProtocol | None = None,
) -> BaseTool:
    """Build a fakeable ``get_portfolio_tokens`` tool."""

    resolved: AlchemyPortfolioClientProtocol = client or AlchemyPortfolioClient(
        secret_store=deps.secret_store,
        api_key_secret_path=deps.api_key_secret_path,
    )

    def get_portfolio_tokens_tool(
        wallet_address: str,
        chains: list[str],
        with_metadata: bool = True,
        with_prices: bool = True,
        include_native_tokens: bool = True,
        include_erc20_tokens: bool = True,
        page_key: str | None = None,
    ) -> dict[str, Any]:
        return get_portfolio_tokens(
            wallet_address=wallet_address,
            chains=chains,
            with_metadata=with_metadata,
            with_prices=with_prices,
            include_native_tokens=include_native_tokens,
            include_erc20_tokens=include_erc20_tokens,
            page_key=page_key,
            client=resolved,
        )

    return StructuredTool.from_function(
        func=get_portfolio_tokens_tool,
        name="get_portfolio_tokens",
        description=(
            "List fungible tokens (native + ERC-20) for one wallet via Alchemy Portfolio API. "
            "Up to 5 Mercury chains per request (ethereum, base, arbitrum, optimism)."
        ),
    )
