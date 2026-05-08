"""Juno setuptools plugin: base URL resolution and runner selection."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from juno.agents.registry import SubagentSpec
from juno.plugins import JunoPluginContext
from juno.settings import Settings

from mercury.graph.runtime import GraphRuntime
from mercury.juno.manifest import load_mercury_juno_manifest
from mercury.juno.plugin import (
    create_plugin,
    resolve_mercury_base_url_from_settings,
)
from mercury.juno.runners import LocalMercuryAssistantRunner, MercuryAssistantRunner


@pytest.fixture
def mercury_ctx(monkeypatch: pytest.MonkeyPatch) -> JunoPluginContext:
    monkeypatch.delenv("MERCURY_BASE_URL", raising=False)
    monkeypatch.delenv("JUNO_DISABLED_ASSISTANTS", raising=False)
    s = Settings(
        mercury_runner_mode="http",
        mercury_base_url="https://settings.test",
        mercury_http_path="/v1/mercury/invoke",
        mercury_request_body_mode="flat",
    )
    return JunoPluginContext(settings=s)


def test_resolve_mercury_base_url_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MERCURY_BASE_URL", " https://env.test ")
    m = load_mercury_juno_manifest()
    s = Settings(mercury_base_url="https://settings.test")
    assert resolve_mercury_base_url_from_settings(m, s) == "https://env.test"


def test_resolve_mercury_base_url_falls_back_to_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MERCURY_BASE_URL", raising=False)
    m = load_mercury_juno_manifest()
    s = Settings(mercury_base_url="https://fallback.test")
    assert resolve_mercury_base_url_from_settings(m, s) == "https://fallback.test"


def test_resolve_mercury_base_url_empty_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MERCURY_BASE_URL", raising=False)
    m = load_mercury_juno_manifest()
    s = Settings(mercury_base_url="")
    with pytest.raises(ValueError, match="base URL"):
        resolve_mercury_base_url_from_settings(m, s)


def test_create_plugin_http_uses_http_runner(monkeypatch: pytest.MonkeyPatch, mercury_ctx: JunoPluginContext) -> None:
    captured: dict[str, object] = {}

    class _CaptureMercuryRunner(MercuryAssistantRunner):
        def __init__(self, base_url: str, **kwargs: object) -> None:
            captured["base_url"] = base_url
            captured["kwargs"] = kwargs

    fake_graph = MagicMock(name="compiled_sub")

    monkeypatch.setattr("mercury.juno.plugin.MercuryAssistantRunner", _CaptureMercuryRunner)
    monkeypatch.setattr("mercury.juno.plugin.build_mercury_juno_subagent", lambda **kw: fake_graph)
    monkeypatch.setattr(
        "mercury.juno.plugin.default_mercury_subagent_spec",
        lambda graph: SubagentSpec(name="mercury", description="d", graph=graph),
    )

    specs = create_plugin(mercury_ctx)
    assert len(specs) == 1
    assert specs[0].name == "mercury"
    assert captured["base_url"] == "https://settings.test"
    assert isinstance(captured["kwargs"], dict)
    assert captured["kwargs"].get("http_path") == "/v1/mercury/invoke"


def test_create_plugin_local_uses_standalone_runtime(
    monkeypatch: pytest.MonkeyPatch,
    mercury_ctx: JunoPluginContext,
) -> None:
    fake_rt = MagicMock(spec=GraphRuntime)
    monkeypatch.setattr("mercury.juno.plugin.build_standalone_graph_runtime", lambda: fake_rt)

    mercury_ctx.settings.mercury_runner_mode = "local"  # type: ignore[misc]

    captured_runner: dict[str, object] = {}

    class _CaptureLocal(LocalMercuryAssistantRunner):
        def __init__(self, runtime: object, **kwargs: object) -> None:
            super().__init__(runtime, **kwargs)  # type: ignore[arg-type]
            captured_runner["runtime"] = runtime

    fake_graph = MagicMock(name="compiled_sub")

    monkeypatch.setattr("mercury.juno.plugin.LocalMercuryAssistantRunner", _CaptureLocal)
    monkeypatch.setattr("mercury.juno.plugin.build_mercury_juno_subagent", lambda **kw: fake_graph)
    monkeypatch.setattr(
        "mercury.juno.plugin.default_mercury_subagent_spec",
        lambda graph: SubagentSpec(name="mercury", description="d", graph=graph),
    )

    specs = create_plugin(mercury_ctx)
    assert len(specs) == 1
    assert captured_runner["runtime"] is fake_rt
