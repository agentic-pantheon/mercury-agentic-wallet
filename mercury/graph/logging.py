"""Structured logging for Mercury LangGraph execution (pairs with mercury.service.logging)."""

from __future__ import annotations

import logging
from typing import Any


def get_graph_logger() -> logging.Logger:
    """Logger for LangGraph orchestration (`mercury.graph`)."""

    return logging.getLogger("mercury.graph")


def log_graph_event(
    event: str,
    *,
    level: int = logging.INFO,
    summary: str | None = None,
    **fields: Any,
) -> None:
    """Emit ``mercury graph:<event> | <summary> | {<json>}``."""

    from mercury.service.logging import format_mercury_log_line

    message = format_mercury_log_line(scope="graph", event=event, fields=fields, summary=summary)
    get_graph_logger().log(level, message)
