"""Logging configuration behaves under pytest and uvicorn-like root handlers."""

from __future__ import annotations

import logging
from typing import cast
from unittest.mock import patch

from mercury.graph.state import MercuryState
from mercury.invoke import MercuryInvoker
from mercury.models.errors import internal_error
from mercury.service.logging import configure_service_logging, parse_mercury_log_level
from mercury.service.models import MercuryInvokeRequest


def test_parse_mercury_log_level_recognizes_info() -> None:
    assert parse_mercury_log_level("INFO") == logging.INFO


def test_parse_mercury_log_level_unknown_falls_back_to_debug() -> None:
    assert parse_mercury_log_level("NOT_A_LEVEL") == logging.DEBUG


def test_configure_service_logging_aligns_existing_handler_levels() -> None:
    """Handlers created at INFO still emit DEBUG records after Mercury's sync."""
    root = logging.getLogger()
    saved_handlers = [*root.handlers]
    prev_level = root.level
    try:
        root.handlers.clear()
        h = logging.StreamHandler()
        h.setLevel(logging.INFO)
        root.addHandler(h)
        root.setLevel(logging.INFO)

        configure_service_logging(level=logging.DEBUG)

        assert root.level == logging.DEBUG
        assert h.level == logging.DEBUG
    finally:
        root.handlers.clear()
        root.setLevel(prev_level)
        for h_saved in saved_handlers:
            root.addHandler(h_saved)


def test_invoke_response_log_contains_error_summary() -> None:
    service_events: list[dict[str, object]] = []

    def capture_service_event(event: str, **kwargs: object) -> None:
        service_events.append(dict(event=event, **kwargs))

    class _FailRuntime:
        def invoke(self, state: MercuryState) -> MercuryState:
            out = dict(state)
            out["error"] = internal_error(
                message="simulated_failure",
                stage="prepare_erc20_transaction",
            )
            return cast(MercuryState, out)

    payload = MercuryInvokeRequest(
        user_id="u1",
        wallet_id="primary",
        intent={"kind": "erc20_transfer"},
    )

    invoker = MercuryInvoker(_FailRuntime())
    with patch("mercury.invoke.log_service_event", side_effect=capture_service_event):
        resp = invoker.invoke(payload)

    assert resp.status == "failed"
    assert resp.error is not None

    response_events = [e for e in service_events if e.get("event") == "invoke_response"]
    assert len(response_events) == 1
    fields = response_events[0]

    assert fields["intent_kind"] == "erc20_transfer"
    assert fields["error_code"] == "internal_error"
    assert fields["error_stage"] == "prepare_erc20_transaction"
    assert "error_message" in fields
