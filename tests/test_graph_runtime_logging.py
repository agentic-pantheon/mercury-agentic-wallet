"""Graph runtime emits structured mercury.graph logs."""

from __future__ import annotations

import json
import logging
import re

import pytest
from mercury.config import MercurySettings
from mercury.graph.agent import build_graph
from mercury.graph.runtime import MercuryGraphRuntime

from tests.test_graph_readonly_execution import WALLET, _fake_registry

_ANSI_ESC = re.compile(r"\x1b\[[0-9;:]*m")


@pytest.fixture(autouse=True)
def _no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stable caplog assertions (ANSI off even if stderr is a TTY)."""

    monkeypatch.setenv("NO_COLOR", "1")


def test_mercury_graph_runtime_logs_run_and_each_node(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="mercury.graph")
    compiled = build_graph(_fake_registry()).compile()
    runtime = MercuryGraphRuntime(
        read_graph=compiled,
        erc20_graph=compiled,
        native_graph=compiled,
        swap_graph=compiled,
        runtime_settings=MercurySettings(graph_node_logging=True),
    )

    result = runtime.invoke(
        {
            "raw_input": {"kind": "native_balance", "wallet_address": WALLET},
            "request_id": "req-graph-test",
        }
    )

    records = [
        _strip_ansi(rec.message).strip()
        for rec in caplog.records
        if rec.name == "mercury.graph"
    ]
    payloads = [_loads_json_maybe(m) for m in records]
    events_all = [
        payload["event"]
        for payload in payloads
        if isinstance(payload, dict) and "event" in payload
    ]

    assert events_all[0] == "graph_run_start"
    finishes = [
        str(payload["node"])
        for payload in payloads
        if isinstance(payload, dict) and payload.get("event") == "graph_node_finished"
    ]
    expected = {"parse_intent", "resolve_chain", "get_native_balance", "format_response"}
    assert set(finishes) == expected
    assert len(finishes) == 4

    chain_ref = result.get("chain_reference")
    assert chain_ref is not None
    assert chain_ref.name == "ethereum"


def _strip_ansi(message: str) -> str:
    return _ANSI_ESC.sub("", message)


def _loads_json_maybe(raw: str) -> dict[str, object] | str:
    text = raw.strip()
    if " | " in text:
        _, maybe_json = text.rsplit(" | ", 1)
        maybe_json = maybe_json.strip()
        if maybe_json.startswith("{"):
            try:
                out = json.loads(maybe_json)
                if isinstance(out, dict):
                    return out
            except json.JSONDecodeError:
                pass
    try:
        out = json.loads(text)
        if isinstance(out, dict):
            return out
    except json.JSONDecodeError:
        pass
    return raw
