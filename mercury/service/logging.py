"""Structured logging helpers with service-boundary redaction."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

REDACTION = "<redacted>"

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "private_key",
    "raw_transaction",
    "rpc_url",
    "secret",
    "signature",
    "token",
)
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
_SECRET_PATH_PATTERN = re.compile(r"\bmercury/(?:rpc|apis|wallets)/[A-Za-z0-9_./-]+\b")
_ONECLAW_TOKEN_PATTERN = re.compile(r"(?i)\b(?:oneclaw|1claw|api[_-]?key|bearer)\s*[:=]\s*\S+")
_LONG_HEX_PATTERN = re.compile(r"\b0x[a-fA-F0-9]{96,}\b")

_RESET = "\033[0m"
_DIM = "\033[2m"

_COLOR_BY_LEVELNO: dict[int, str] = {
    logging.DEBUG: "\033[90m",
    logging.INFO: "\033[36m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[35m",
}

_PLAIN_LINE = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_PLAIN_DATEFMT = "%Y-%m-%d %H:%M:%S"


def parse_mercury_log_level(level_name: str) -> int:
    """Resolve a logging level string to an int (default DEBUG for unknown labels)."""

    key = level_name.strip().upper() or "DEBUG"
    resolved = getattr(logging, key, logging.DEBUG)
    if isinstance(resolved, int):
        return resolved
    return logging.DEBUG


def stderr_supports_color() -> bool:
    """True when stderr is a TTY and NO_COLOR is unset (https://no-color.org/)."""

    return bool(sys.stderr.isatty() and not os.environ.get("NO_COLOR"))


class MercuryColoredFormatter(logging.Formatter):
    """ANSI-colored single-line formatter; plain text when coloring is disabled."""

    def __init__(self, *, use_color: bool) -> None:
        super().__init__(fmt=_PLAIN_LINE, datefmt=_PLAIN_DATEFMT)
        self._use_color = use_color
        self._plain = logging.Formatter(fmt=_PLAIN_LINE, datefmt=_PLAIN_DATEFMT)

    def format(self, record: logging.LogRecord) -> str:
        plain = self._plain.format(record)
        if not self._use_color:
            return plain

        parts = plain.split(" | ", 3)
        if len(parts) != 4:
            return plain
        asctime_s, level_s, name_s, message_s = parts
        color = _COLOR_BY_LEVELNO.get(record.levelno, _COLOR_BY_LEVELNO[logging.INFO])
        return (
            f"{_DIM}{asctime_s}{_RESET} | {color}{level_s}{_RESET} | "
            f"{_DIM}{name_s}{_RESET} | {message_s}"
        )


_UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access")


def configure_service_logging(*, level: int = logging.DEBUG) -> None:
    """Align root handlers and Mercury loggers with ``level``.

    Under uvicorn, root handlers usually exist already and keep their own
    ``handler.level`` unless updated here — otherwise DEBUG would be filtered out.
    """

    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        for handler in root.handlers:
            handler.setLevel(level)

        logging.getLogger("mercury.graph").setLevel(logging.NOTSET)
        logging.getLogger("mercury.service").setLevel(logging.NOTSET)
        for uv_name in _UVICORN_LOGGER_NAMES:
            logging.getLogger(uv_name).setLevel(level)
        return

    fmt_color = MercuryColoredFormatter(use_color=stderr_supports_color())
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(fmt_color)
    root.addHandler(handler)
    root.setLevel(level)

    logging.getLogger("mercury.graph").setLevel(logging.NOTSET)
    logging.getLogger("mercury.service").setLevel(logging.NOTSET)


def get_service_logger() -> logging.Logger:
    """Return Mercury's service logger."""

    return logging.getLogger("mercury.service")


def redact_value(value: Any) -> Any:
    """Recursively redact values that should not cross HTTP or log boundaries."""

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                redacted[key_text] = REDACTION
            else:
                redacted[key_text] = redact_value(item)
        return redacted
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [redact_value(item) for item in value]
    return value


def redact_error_message(error: BaseException | str) -> str:
    """Return a sanitized error message for public responses."""

    message = str(error) or "Mercury request failed."
    return str(redact_value(message))


_LOG_SUMMARY_MAX = 520


def format_mercury_log_line(
    *,
    scope: str,
    event: str,
    fields: Mapping[str, Any],
    summary: str | None = None,
) -> str:
    """Build one log line: ``mercury <scope>:<event> | <summary> | {<json>}``.

    ``scope`` is ``service`` (HTTP, invoke, envelopes) or ``graph`` (LangGraph nodes).
    Machine-readable fields stay in JSON (redacted); grep for ``mercury graph:`` or
    ``mercury service:`` and the event name.
    """

    payload = {"event": event, **redact_value(dict(fields))}
    body = json.dumps(payload, sort_keys=True, default=str)
    head = f"mercury {scope}:{event}"
    if summary:
        if len(summary) <= _LOG_SUMMARY_MAX:
            clip = summary
        else:
            clip = summary[: _LOG_SUMMARY_MAX - 3] + "..."
        return f"{head} | {clip} | {body}"
    return f"{head} | {body}"


def log_service_event(
    event: str,
    *,
    level: int = logging.INFO,
    logger: logging.Logger | None = None,
    summary: str | None = None,
    **fields: Any,
) -> None:
    """Emit a structured Mercury service log (``mercury service:<event> | ...``)."""

    target = logger or get_service_logger()
    message = format_mercury_log_line(scope="service", event=event, fields=fields, summary=summary)
    target.log(level, message)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _redact_text(text: str) -> str:
    redacted = text
    for pattern in (_URL_PATTERN, _SECRET_PATH_PATTERN, _ONECLAW_TOKEN_PATTERN, _LONG_HEX_PATTERN):
        redacted = pattern.sub(REDACTION, redacted)
    return redacted
