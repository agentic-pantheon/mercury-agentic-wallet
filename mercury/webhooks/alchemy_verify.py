"""Verify Alchemy Notify webhook `X-Alchemy-Signature` (HMAC-SHA256 over raw body)."""

from __future__ import annotations

import hashlib
import hmac


def is_valid_signature_for_string_body(body: bytes, signature: str, signing_key: str) -> bool:
    """Return True when ``signature`` matches HMAC-SHA256(signing_key, body) as hex.

    ``body`` must be the **exact** raw request bytes Alchemy signed. Comparisons use
    :func:`hmac.compare_digest` for constant-time equality on the hex digest strings.
    Empty or malformed ``signature`` values are rejected without raising.
    """

    if not signing_key:
        return False
    if not signature or not isinstance(signature, str):
        return False
    sig = signature.strip()
    if sig.lower().startswith("0x"):
        sig = sig[2:]
    sig = sig.strip().lower()
    if not sig or any(ch not in "0123456789abcdef" for ch in sig):
        return False

    digest = hmac.new(
        signing_key.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    if len(sig) != len(digest):
        return False
    return hmac.compare_digest(digest, sig)
