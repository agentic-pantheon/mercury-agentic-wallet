"""Shared LangGraph helpers for intent-to-transaction preparation nodes."""

from __future__ import annotations

from pydantic import ValidationError

from mercury.graph.state import MercuryState
from mercury.models.errors import normalize_exception, validation_failed_from_pydantic


def state_for_preparation_error(exc: Exception, *, stage: str) -> MercuryState:
    """Normalize ValidationError vs generic failures into Mercury ``error`` state."""

    if isinstance(exc, ValidationError):
        return {"error": validation_failed_from_pydantic(exc, stage=stage)}
    return {"error": normalize_exception(exc, stage=stage)}
