"""Setuptools entry-point target for Juno (group ``juno.assistants``)."""

from mercury.juno.plugin import create_plugin

__all__ = ["create_plugin"]
