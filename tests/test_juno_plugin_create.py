"""Juno setuptools plugin: always uses LocalMercuryAssistantRunner."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from juno.agents.registry import SubagentSpec
from juno.plugins import JunoPluginContext
from juno.settings import Settings

from mercury.graph.runtime import GraphRuntime
from mercury.juno.plugin import create_plugin
from mercury.juno.runners import LocalMercuryAssistantRunner


@pytest.fixture
def mercury_ctx(monkeypatch: pytest.MonkeyPatch) -> JunoPluginContext:
    monkeypatch.delenv("MERCURY_BASE_URL", raising=False)
    monkeypatch.delenv("JUNO_DISABLED_ASSISTANTS", raising=False)
    s = Settings()
    return JunoPluginContext(settings=s)


def test_create_plugin_uses_local_runner_with_standalone_runtime(
    monkeypatch: pytest.MonkeyPatch,
    mercury_ctx: JunoPluginContext,
) -> None:
    fake_rt = MagicMock(spec=GraphRuntime)
    monkeypatch.setattr("mercury.juno.plugin.build_standalone_graph_runtime", lambda: fake_rt)

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
    assert specs[0].name == "mercury"
    assert captured_runner["runtime"] is fake_rt
