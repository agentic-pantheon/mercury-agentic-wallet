"""Read-only tool registry for graph execution."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from langchain_core.tools import BaseTool

from mercury.tools.evm import ProviderFactoryLike


class ReadOnlyToolRegistry:
    """Small registry that lets graph nodes execute fakeable read-only tools."""

    def __init__(self, tools: Iterable[BaseTool] = ()) -> None:
        self._tools = {tool.name: tool for tool in tools}

    @classmethod
    def from_provider_factory(
        cls,
        provider_factory: ProviderFactoryLike,
        *,
        alchemy_prices: Any | None = None,
    ) -> ReadOnlyToolRegistry:
        """Create a registry bound to a provider factory and optional Alchemy HTTP tools."""

        from mercury.tools import create_readonly_tools
        from mercury.tools.token_prices import AlchemyPricesToolDeps, create_alchemy_token_prices_tool

        tools = list(create_readonly_tools(provider_factory))
        if alchemy_prices is not None:
            if not isinstance(alchemy_prices, AlchemyPricesToolDeps):
                msg = "alchemy_prices must be AlchemyPricesToolDeps when provided."
                raise TypeError(msg)
            tools.append(create_alchemy_token_prices_tool(alchemy_prices))
        return cls(tools)

    def execute(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Execute a registered read-only tool."""

        try:
            tool = self._tools[tool_name]
        except KeyError as exc:
            msg = f"Read-only tool '{tool_name}' is not configured."
            raise ValueError(msg) from exc

        result = tool.invoke(tool_input)
        if isinstance(result, dict):
            return result
        msg = f"Read-only tool '{tool_name}' returned an unsupported result."
        raise TypeError(msg)

    def names(self) -> set[str]:
        """Return configured tool names."""

        return set(self._tools)
