"""Juno assistant plugin factory (``juno.assistants`` setuptools entry points)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from juno.agents.registry import SubagentSpec

from mercury.service.dependencies import build_standalone_graph_runtime
from mercury.juno.manifest import load_mercury_juno_manifest
from mercury.juno.runners import LocalMercuryAssistantRunner
from mercury.juno.specs import default_mercury_subagent_spec
from mercury.juno.subagent import build_mercury_juno_subagent

if TYPE_CHECKING:
    from juno.plugins import JunoPluginContext


def create_plugin(ctx: "JunoPluginContext") -> Sequence[SubagentSpec]:
    manifest = load_mercury_juno_manifest()
    if manifest.runner != "mercury":
        msg = f"Expected runner 'mercury' in packaged manifest, got {manifest.runner!r}."
        raise ValueError(msg)

    runner = LocalMercuryAssistantRunner(build_standalone_graph_runtime())
    sub = build_mercury_juno_subagent(model=ctx.settings.openai_model, manifest=manifest, runner=runner)
    return (default_mercury_subagent_spec(sub),)


__all__ = ["create_plugin"]
