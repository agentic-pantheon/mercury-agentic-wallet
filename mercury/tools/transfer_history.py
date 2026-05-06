"""Read-only LangChain tool for Alchemy ``alchemy_getAssetTransfers``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from mercury.alchemy.transfers import AlchemyTransfersClient, AlchemyTransfersClientProtocol
from mercury.custody.oneclaw import SecretStore
from mercury.tools.schemas import TransferHistoryToolInput


@dataclass(frozen=True)
class AlchemyTransfersToolDeps:
    """Injectable deps for ``get_transfer_history`` (1Claw path only, never the raw API key)."""

    secret_store: SecretStore
    api_key_secret_path: str


def get_transfer_history(
    *,
    chain: str,
    wallet_address: str,
    direction: str = "both",
    categories: list[str] | None = None,
    from_block: str | int | None = None,
    to_block: str | int | None = None,
    max_count: int = 100,
    page_key: str | None = None,
    with_metadata: bool = True,
    exclude_zero_value: bool = True,
    client: AlchemyTransfersClientProtocol,
) -> dict[str, Any]:
    """Fetch normalized historical transfers for one wallet on one Mercury chain."""

    parsed = TransferHistoryToolInput.model_validate(
        {
            "chain": chain,
            "wallet_address": wallet_address,
            "direction": direction,
            "categories": categories,
            "from_block": from_block,
            "to_block": to_block,
            "max_count": max_count,
            "page_key": page_key,
            "with_metadata": with_metadata,
            "exclude_zero_value": exclude_zero_value,
        }
    )
    return client.fetch_transfers_for_wallet(
        mercury_chain=parsed.chain,
        wallet_address=parsed.wallet_address,
        direction=parsed.direction,
        categories=parsed.categories,
        from_block=parsed.from_block,
        to_block=parsed.to_block,
        max_count=parsed.max_count,
        page_key=parsed.page_key,
        with_metadata=parsed.with_metadata,
        exclude_zero_value=parsed.exclude_zero_value,
    )


def create_alchemy_transfer_history_tool(
    deps: AlchemyTransfersToolDeps,
    *,
    client: AlchemyTransfersClientProtocol | None = None,
) -> BaseTool:
    """Build a fakeable ``get_transfer_history`` tool."""

    resolved: AlchemyTransfersClientProtocol = client or AlchemyTransfersClient(
        secret_store=deps.secret_store,
        api_key_secret_path=deps.api_key_secret_path,
    )

    def get_transfer_history_tool(
        chain: str,
        wallet_address: str,
        direction: str = "both",
        categories: list[str] | None = None,
        from_block: str | int | None = None,
        to_block: str | int | None = None,
        max_count: int = 100,
        page_key: str | None = None,
        with_metadata: bool = True,
        exclude_zero_value: bool = True,
    ) -> dict[str, Any]:
        return get_transfer_history(
            chain=chain,
            wallet_address=wallet_address,
            direction=direction,
            categories=categories,
            from_block=from_block,
            to_block=to_block,
            max_count=max_count,
            page_key=page_key,
            with_metadata=with_metadata,
            exclude_zero_value=exclude_zero_value,
            client=resolved,
        )

    return StructuredTool.from_function(
        func=get_transfer_history_tool,
        name="get_transfer_history",
        description=(
            "List on-chain transfers for one wallet via Alchemy Transfers API (JSON-RPC). "
            "Supports ethereum, base, arbitrum, and optimism. "
            "Use direction incoming, outgoing, or both; echo page_key within TTL for the next page "
            "(pagination is not available when direction is both)."
        ),
    )
