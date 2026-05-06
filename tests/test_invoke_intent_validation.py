"""Upfront invoke intent validation (`validate_invoke_intent` + runtime preflight)."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from mercury.graph.intent_validation import validate_invoke_intent
from mercury.graph.runtime import MercuryGraphRuntime
from mercury.known_addresses.book import lookup_address


def test_contract_read_string_abi_returns_abi_remediation() -> None:
    state, err = validate_invoke_intent(
        {
            "raw_input": {
                "kind": "contract_read",
                "chain": "base",
                "contract_address": "0x0555E30Da8F98308edB960aa94C0DBA30dd6B0c2",
                "abi_fragment": "function balanceOf(address) view returns (uint256)",
                "function_name": "balanceOf",
                "args": ["0xc1923710468607b8b7db38a6afbb9b432744390c"],
            },
        }
    )
    assert state is None
    assert err is not None
    assert err.code in {"unsupported_intent", "validation_failed"}
    details = err.details
    assert "abi_fragment_example_balanceOf" in details
    assert details.get("abi_fragment_expected") == "list[dict] (JSON ABI entries)"
    assert "alternate_intent_suggestion" in details


def test_erc20_balance_symbol_token_suggests_catalog_address() -> None:
    wallet = "0xc1923710468607b8b7db38a6afbb9b432744390c"
    state, err = validate_invoke_intent(
        {
            "raw_input": {
                "kind": "erc20_balance",
                "chain": "base",
                "token_address": "WBTC",
                "wallet_address": wallet,
            },
        }
    )
    assert state is None
    assert err is not None
    assert err.code in {"validation_failed", "unsupported_intent"}
    suggestion = err.details.get("known_address_catalog_suggestion")
    assert isinstance(suggestion, dict)
    assert suggestion.get("field") == "token_address"
    assert suggestion.get("resolved_checksum_address") == lookup_address("base", "token", "WBTC")


def test_runtime_skips_graph_when_validation_fails(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="mercury.graph")
    mock_graph = MagicMock()
    mock_graph.invoke.side_effect = AssertionError("graph.invoke must not run")
    mock_graph.stream = None

    runtime = MercuryGraphRuntime(
        read_graph=mock_graph,
        erc20_graph=mock_graph,
        native_graph=mock_graph,
        swap_graph=mock_graph,
    )
    out = runtime.invoke(
        {
            "request_id": "req-preflight",
            "raw_input": {
                "kind": "contract_read",
                "chain": "base",
                "contract_address": "0x0555E30Da8F98308edB960aa94C0DBA30dd6B0c2",
                "abi_fragment": "function balanceOf(address) view returns (uint256)",
                "function_name": "balanceOf",
                "args": ["0xc1923710468607b8b7db38a6afbb9b432744390c"],
            },
        }
    )

    mock_graph.invoke.assert_not_called()
    assert out.get("error") is not None
    assert out.get("response_text")

    logged = any(
        "invoke_intent_validation_failed" in rec.message
        for rec in caplog.records
        if rec.name == "mercury.graph"
    )
    assert logged, "expected invoke_intent_validation_failed log"
