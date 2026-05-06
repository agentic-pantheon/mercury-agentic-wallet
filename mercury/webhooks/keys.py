"""Resolve the Alchemy webhook signing key (1Claw secret or test overlay)."""

from __future__ import annotations

from fastapi import Request

from mercury.config import MercurySettings
from mercury.custody import SecretStore


def resolve_alchemy_webhook_signing_key(
    *,
    request: Request,
    settings: MercurySettings,
    secret_store: SecretStore | None = None,
) -> str:
    """Return the raw signing key string for HMAC verification.

    When ``request.app.state.alchemy_webhook_signing_key`` is a non-empty string (used in
    tests), that value wins. Otherwise load ``settings.alchemy_webhook_signing_key_secret_path``
    via ``secret_store``. ``secret_store`` must be provided for the 1Claw path in production.

    Raises
    ------
    ValueError
        When no overlay is set and the secret path is blank or ``secret_store`` is missing.
    """

    raw_overlay = getattr(request.app.state, "alchemy_webhook_signing_key", None)
    if isinstance(raw_overlay, str) and raw_overlay.strip():
        return raw_overlay.strip()
    path = settings.alchemy_webhook_signing_key_secret_path.strip()
    if not path:
        raise ValueError("alchemy_webhook_signing_key_secret_path is empty")
    if secret_store is None:
        raise ValueError("secret_store is required when no signing key overlay is configured")
    return secret_store.get_secret(path).reveal()
