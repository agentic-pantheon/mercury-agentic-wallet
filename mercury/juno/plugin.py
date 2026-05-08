"""Juno assistant plugin factory (``juno.assistants`` setuptools entry points)."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING

from juno.agents.registry import SubagentSpec

from mercury.service.dependencies import build_standalone_graph_runtime
from mercury.juno.manifest import load_mercury_juno_manifest
from mercury.juno.runners import LocalMercuryAssistantRunner, MercuryAssistantRunner
from mercury.juno.specs import default_mercury_subagent_spec
from mercury.juno.subagent import build_mercury_juno_subagent

if TYPE_CHECKING:
    from juno.plugins import JunoPluginContext


def resolve_mercury_base_url_from_settings(manifest, settings) -> str:
    """Prefer ``os.environ[manifest.base_url_env]``, then Juno ``Settings.mercury_base_url``."""

    env_val = os.environ.get(manifest.base_url_env, "").strip()
    if env_val:
        return env_val.rstrip("/")
    if manifest.runner == "mercury":
        base = settings.mercury_base_url.strip()
        if base:
            return base.rstrip("/")
    raise ValueError(
        f"Assistant {manifest.runner!r} base URL is empty. Set environment variable "
        f"{manifest.base_url_env!r} (or for Mercury, MERCURY_BASE_URL via Juno Settings).",
    )


def create_plugin(ctx: "JunoPluginContext") -> Sequence[SubagentSpec]:
    manifest = load_mercury_juno_manifest()
    if manifest.runner != "mercury":
        msg = f"Expected runner 'mercury' in packaged manifest, got {manifest.runner!r}."
        raise ValueError(msg)

    settings = ctx.settings

    if settings.mercury_runner_mode == "local":
        runner = LocalMercuryAssistantRunner(build_standalone_graph_runtime())
    else:
        base_url = resolve_mercury_base_url_from_settings(manifest, settings)
        runner = MercuryAssistantRunner(
            base_url,
            http_path=settings.mercury_http_path,
            request_body_mode=settings.mercury_request_body_mode,
        )

    sub = build_mercury_juno_subagent(model=settings.openai_model, manifest=manifest, runner=runner)
    return (default_mercury_subagent_spec(sub),)


__all__ = ["create_plugin", "resolve_mercury_base_url_from_settings"]
