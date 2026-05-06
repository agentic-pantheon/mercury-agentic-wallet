from typing import Any

from langchain_core.tools import StructuredTool

from mercury.graph.agent import build_graph
from mercury.tools.registry import ReadOnlyToolRegistry

WALLET = "0x000000000000000000000000000000000000dEaD"
TOKEN = "0x000000000000000000000000000000000000cafE"


def test_fake_portfolio_tokens_tool_formats_row_and_page_hint() -> None:
    def get_portfolio_tokens(
        wallet_address: str,
        chains: list[str],
        with_metadata: bool = True,
        with_prices: bool = True,
        include_native_tokens: bool = True,
        include_erc20_tokens: bool = True,
        page_key: str | None = None,
    ) -> dict[str, Any]:
        """Fake portfolio snapshot for graph execution tests."""
        assert wallet_address == WALLET
        assert chains == ["ethereum"]
        assert page_key is None
        return {
            "wallet_address": wallet_address,
            "tokens": [
                {
                    "wallet_address": wallet_address,
                    "network": "eth-mainnet",
                    "mercury_chain": "ethereum",
                    "token_address": None,
                    "balance": "1",
                    "metadata": {"decimals": 18, "logo": None, "name": "Ether", "symbol": "ETH"},
                    "prices": [{"currency": "usd", "value": "2000", "lastUpdatedAt": None}],
                    "error": None,
                }
            ],
            "page_key": "more",
        }

    graph = build_graph(
        ReadOnlyToolRegistry([StructuredTool.from_function(get_portfolio_tokens)])
    ).compile()

    result = graph.invoke(
        {
            "raw_input": {
                "kind": "portfolio_tokens",
                "wallet_address": WALLET,
                "chain": "ethereum",
            }
        }
    )

    assert result["selected_tool_name"] == "get_portfolio_tokens"
    text = result["response_text"].lower()
    assert "2000 usd" in text
    assert "next page" in text
    assert "0.000000000000000001" in text
    assert "demo-api-key" not in text


def test_fake_transfer_history_tool_formats_summary_and_page_hint() -> None:
    def get_transfer_history(
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
        """Fake transfer history rows for graph execution tests."""
        assert chain == "ethereum"
        assert wallet_address == WALLET
        assert direction == "incoming"
        assert page_key == "cont"
        return {
            "wallet_address": wallet_address,
            "mercury_chain": "ethereum",
            "network": "eth-mainnet",
            "transfers": [
                {
                    "hash": "0xaa",
                    "block_num": 10,
                    "category": "erc20",
                    "from_address": "0x0000000000000000000000000000000000000001",
                    "to_address": wallet_address,
                    "asset": "USDC",
                    "value": None,
                    "raw_contract": None,
                    "metadata": None,
                    "network": "eth-mainnet",
                    "mercury_chain": "ethereum",
                }
            ],
            "page_key": "more",
        }

    graph = build_graph(
        ReadOnlyToolRegistry([StructuredTool.from_function(get_transfer_history)])
    ).compile()

    result = graph.invoke(
        {
            "raw_input": {
                "kind": "transfer_history",
                "wallet_address": WALLET,
                "chain": "ethereum",
                "direction": "incoming",
                "page_key": "cont",
            }
        }
    )

    assert result["selected_tool_name"] == "get_transfer_history"
    text = result["response_text"].lower()
    assert "erc20" in text
    assert "next page" in text or "page_key" in text
    assert "demo-api-key" not in text


def test_fake_token_prices_tool_formats_mixed_success_and_error_rows() -> None:
    def get_token_prices(tokens: list[dict[str, Any]]) -> dict[str, Any]:
        """Fake Alchemy-normalized token price payload."""

        assert len(tokens) == 2
        return {
            "tokens": [
                {
                    "network": "base-mainnet",
                    "mercury_chain": "base",
                    "address": TOKEN,
                    "prices": [{"currency": "usd", "value": "1.0", "lastUpdatedAt": None}],
                    "error": None,
                },
                {
                    "network": "eth-mainnet",
                    "mercury_chain": "ethereum",
                    "address": WALLET,
                    "prices": [],
                    "error": "no liquidity",
                },
            ]
        }

    graph = build_graph(
        ReadOnlyToolRegistry([StructuredTool.from_function(get_token_prices)])
    ).compile()

    result = graph.invoke(
        {
            "raw_input": {
                "kind": "token_prices",
                "tokens": [
                    {"chain": "base", "token_address": TOKEN},
                    {"chain": "ethereum", "token_address": WALLET},
                ],
            }
        }
    )

    assert result["selected_tool_name"] == "get_token_prices"
    text = result["response_text"].lower()
    assert "1.0 usd" in text
    assert "no liquidity" in text


def test_fake_native_balance_tool_result_is_formatted() -> None:
    graph = build_graph(_fake_registry()).compile()

    result = graph.invoke({"raw_input": {"kind": "native_balance", "wallet_address": WALLET}})

    assert result["chain_reference"].name == "ethereum"
    assert result["selected_tool_name"] == "get_native_balance"
    assert result["tool_input"] == {"chain": "ethereum", "wallet_address": WALLET}
    assert result["tool_result"]["formatted"] == "1.5"
    assert "1.5 ETH on ethereum" in result["response_text"]


def test_fake_erc20_balance_tool_result_is_formatted_on_base() -> None:
    graph = build_graph(_fake_registry()).compile()

    result = graph.invoke(
        {
            "raw_input": {
                "kind": "erc20_balance",
                "chain": "base",
                "token_address": TOKEN.lower(),
                "wallet_address": WALLET.lower(),
            }
        }
    )

    assert result["chain_reference"].name == "base"
    assert result["selected_tool_name"] == "get_erc20_balance"
    assert result["tool_input"] == {
        "chain": "base",
        "token_address": TOKEN,
        "wallet_address": WALLET,
    }
    assert "42 USDC on base" in result["response_text"]


def test_fake_tool_error_becomes_sanitized_response() -> None:
    graph = build_graph(_fake_registry(raise_error=True)).compile()

    result = graph.invoke({"raw_input": {"kind": "native_balance", "wallet_address": WALLET}})

    assert "https://" not in result["response_text"]
    assert "mercury/rpc/" not in result["response_text"]
    assert "I could not complete the read-only request" in result["response_text"]
    assert "<redacted>" in result["response_text"]


def test_value_moving_text_is_rejected_without_tool_execution() -> None:
    graph = build_graph(_fake_registry()).compile()

    result = graph.invoke({"raw_input": "approve USDC for this spender"})

    assert "selected_tool_name" not in result
    assert result["parsed_intent"]["kind"] == "unsupported"
    assert "Value-moving wallet actions are not supported" in result["response_text"]


def _fake_registry(*, raise_error: bool = False) -> ReadOnlyToolRegistry:
    def get_native_balance(chain: str, wallet_address: str) -> dict[str, Any]:
        """Read a fake native balance."""

        if raise_error:
            raise RuntimeError("missing https://rpc.example.invalid mercury/rpc/ethereum")
        return {
            "chain": chain,
            "chain_id": 1,
            "wallet_address": wallet_address,
            "raw_wei": 1_500_000_000_000_000_000,
            "formatted": "1.5",
            "symbol": "ETH",
        }

    def get_erc20_balance(
        chain: str,
        token_address: str,
        wallet_address: str,
    ) -> dict[str, Any]:
        """Read a fake ERC20 balance."""

        return {
            "chain": chain,
            "chain_id": 8453,
            "token_address": token_address,
            "wallet_address": wallet_address,
            "raw_amount": 42_000_000,
            "formatted": "42",
            "decimals": 6,
            "symbol": "USDC",
            "name": "USD Coin",
        }

    return ReadOnlyToolRegistry(
        [
            StructuredTool.from_function(get_native_balance),
            StructuredTool.from_function(get_erc20_balance),
        ]
    )
