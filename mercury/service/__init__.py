"""FastAPI service exports for Mercury."""

from typing import Any

from mercury.service.models import MercuryInvokeRequest, MercuryInvokeResponse
from mercury.service.pan_agentikit_models import PanAgentEnvelope

__all__ = [
    "MercuryInvokeRequest",
    "MercuryInvokeResponse",
    "PanAgentEnvelope",
    "create_app",
]


def __getattr__(name: str) -> Any:
    """Lazy-import ``create_app`` to avoid service/import cycles."""

    if name == "create_app":
        from mercury.service.api import create_app as _create_app

        return _create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
