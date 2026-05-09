"""Tests for Mercury LangGraph checkpoint factory and dependency wiring."""

from __future__ import annotations

import builtins
from unittest.mock import MagicMock, patch

import pytest
from mercury.config import MercurySettings
from mercury.graph.runtime import GraphRuntime
from mercury.service.api import create_app
from mercury.service.checkpointer import mercury_checkpointer
from mercury.service.dependencies import build_standalone_graph_runtime, get_graph_runtime
from mercury.service.errors import DependencyUnavailableError
from starlette.requests import Request
from starlette.testclient import TestClient


def test_create_app_lifespan_sets_checkpoint_saver_none_when_url_empty() -> None:
    app = create_app(settings=MercurySettings(checkpointer_database_url=""))
    with TestClient(app):
        assert getattr(app.state, "checkpoint_saver", None) is None


def test_mercury_checkpointer_empty_url_yields_none() -> None:
    settings = MercurySettings(checkpointer_database_url="")
    with mercury_checkpointer(settings) as cp:
        assert cp is None


def test_mercury_checkpointer_whitespace_only_yields_none() -> None:
    settings = MercurySettings(checkpointer_database_url="   \t  ")
    with mercury_checkpointer(settings) as cp:
        assert cp is None


def test_mercury_checkpointer_postgres_opens_setup_and_closes() -> None:
    raw = "  postgresql://u:p@db.example:5432/app  "
    stripped = raw.strip()
    mock_saver = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_saver
    mock_ctx.__exit__.return_value = None

    with patch("mercury.service.checkpointer._postgres_saver_type") as saver_type:
        MockPG = saver_type.return_value
        MockPG.from_conn_string.return_value = mock_ctx
        settings = MercurySettings(checkpointer_database_url=raw)
        with mercury_checkpointer(settings) as cp:
            assert cp is mock_saver
            mock_saver.setup.assert_called_once()

        MockPG.from_conn_string.assert_called_once_with(stripped)
        mock_ctx.__enter__.assert_called_once()
        mock_ctx.__exit__.assert_called_once()


def test_mercury_checkpointer_missing_postgres_extra_raises() -> None:
    settings = MercurySettings(checkpointer_database_url="postgresql://x:y@localhost:5432/db")
    real_import = builtins.__import__

    def _guard_import(
        name: str,
        globals_: dict[str, object] | None = None,
        locals_: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "langgraph.checkpoint.postgres":
            raise ImportError("missing optional postgres checkpoints")
        return real_import(name, globals_, locals_, fromlist, level)

    with patch("builtins.__import__", side_effect=_guard_import):
        with pytest.raises(DependencyUnavailableError, match="langgraph Postgres checkpoints"):
            with mercury_checkpointer(settings):
                pass


def _minimal_scope(app: object) -> dict[str, object]:
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "scheme": "http",
        "server": ("test", 80),
        "app": app,
    }


def test_get_graph_runtime_passes_checkpoint_saver_from_app_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.service import dependencies as dep_mod

    captured: dict[str, object] = {}

    def _spy(
        settings: MercurySettings | None = None,
        checkpointer: object | None = None,
    ) -> GraphRuntime:
        captured["checkpointer"] = checkpointer
        return MagicMock(spec=GraphRuntime)

    monkeypatch.setattr(dep_mod, "build_standalone_graph_runtime", _spy)

    app = MagicMock()
    app.state = MagicMock(spec=["settings", "graph_runtime", "checkpoint_saver"])
    app.state.settings = MercurySettings()
    app.state.graph_runtime = None
    mock_cp = MagicMock(name="checkpoint")
    app.state.checkpoint_saver = mock_cp

    request = Request(_minimal_scope(app))
    rt = get_graph_runtime(request)

    assert captured["checkpointer"] is mock_cp
    assert rt is not None
    assert request.app.state.graph_runtime is rt


def test_standalone_runtime_forwards_checkpointer(monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.service import dependencies as dep_mod

    monkeypatch.setattr(dep_mod, "get_secret_store", lambda s: MagicMock())

    captured: dict[str, object] = {}

    def _capture(**kwargs: object) -> MagicMock:
        captured.update(kwargs)
        return MagicMock(spec=GraphRuntime)

    monkeypatch.setattr(dep_mod, "build_default_runtime", _capture)

    cp = MagicMock(name="saver")
    build_standalone_graph_runtime(settings=MercurySettings(), checkpointer=cp)

    assert captured.get("checkpointer") is cp
