"""Mercury/Juno assistant manifest loaded from packaged ``data/juno``."""

from __future__ import annotations

from importlib import resources

import yaml
from pydantic import BaseModel, Field


class JunoAssistantManifest(BaseModel):
    """Subset of Juno's YAML manifest shipped inside the Mercury wheel."""

    runner: str
    base_url_env: str
    system_prompt: str
    requires_session_fields: list[str] = Field(default_factory=list)
    guide_path: str | None = None
    prompt_md_path: str | None = None
    instructions_md: str | None = None


def load_mercury_juno_manifest() -> JunoAssistantManifest:
    """Read ``mercury.yaml`` / ``mercury.md`` via :mod:`importlib.resources`."""

    root = resources.files("mercury.data.juno")
    yaml_raw = (root / "mercury.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(yaml_raw)
    if not isinstance(data, dict):
        msg = "mercury.yaml must contain a YAML mapping at the root."
        raise ValueError(msg)
    md_raw = (root / "mercury.md").read_text(encoding="utf-8")
    return JunoAssistantManifest.model_validate(
        {
            **data,
            "instructions_md": md_raw,
        },
    )


__all__ = ["JunoAssistantManifest", "load_mercury_juno_manifest"]
