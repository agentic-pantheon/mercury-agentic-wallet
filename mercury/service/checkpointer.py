"""LangGraph checkpoint saver lifecycle for the Mercury HTTP app."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

try:
    from langgraph.checkpoint.base import BaseCheckpointSaver
except ImportError:  # pragma: no cover - optional for older langgraph installs
    BaseCheckpointSaver = object  # type: ignore[misc, assignment]

from mercury.config import MercurySettings
from mercury.service.errors import DependencyUnavailableError

_CHECKPOINT_POSTGRES_EXTRA_HINT = (
    "Install optional dependency group ``checkpoint-postgres`` "
    "(package ``langgraph-checkpoint-postgres``), e.g. "
    "``uv pip install -e '.[checkpoint-postgres]'``."
)


def _postgres_saver_type() -> Any:
    """Return the optional Postgres saver class or raise a clear dependency error."""

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:
        raise DependencyUnavailableError(
            "Mercury checkpointer backend requires langgraph Postgres checkpoints. "
            + _CHECKPOINT_POSTGRES_EXTRA_HINT
        ) from exc
    return PostgresSaver


@contextmanager
def mercury_checkpointer(settings: MercurySettings) -> Iterator[BaseCheckpointSaver | None]:  # type: ignore[type-arg]
    """Yield the configured checkpoint saver for the Mercury graph lifetime.

    When ``checkpointer_database_url`` is empty or whitespace-only, yields ``None``
    so compiled graphs use LangGraph's default (no persistent checkpoint backend).

    When a non-empty PostgreSQL DSN is configured, opens :class:`PostgresSaver`,
    runs ``setup()`` once, and yields the saver. The DSN is never logged.

    Embedders that call :func:`~mercury.service.dependencies.build_standalone_graph_runtime`
    outside FastAPI must open their own saver context (or pass ``checkpointer=...``)
    if they need Postgres checkpoints; opening a pool per standalone build leaks
    connections.

    Raises:
        DependencyUnavailableError: If a Postgres URL is set but the optional
            checkpoint Postgres integration is not installed.
    """

    url = settings.checkpointer_database_url.strip()
    if not url:
        yield None
        return

    PostgresSaver = _postgres_saver_type()

    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        yield saver
