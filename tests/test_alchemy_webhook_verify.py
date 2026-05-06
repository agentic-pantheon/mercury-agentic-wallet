"""Tests for Alchemy webhook HMAC verification."""

from __future__ import annotations

import hashlib
import hmac

from mercury.webhooks.alchemy_verify import is_valid_signature_for_string_body


def test_valid_signature_matches_hmac_hex_digest() -> None:
    body = b'{"type":"ADDRESS_ACTIVITY"}'
    key = "whsec_test"
    sig = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert is_valid_signature_for_string_body(body, sig, key) is True


def test_valid_signature_accepts_0x_prefix() -> None:
    body = b'{"hello":"world"}'
    key = "k"
    digest = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert is_valid_signature_for_string_body(body, "0x" + digest, key) is True


def test_tampered_body_fails() -> None:
    body = b'{"type":"ADDRESS_ACTIVITY"}'
    key = "whsec_test"
    sig = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert is_valid_signature_for_string_body(body + b" ", sig, key) is False


def test_wrong_key_fails() -> None:
    body = b"same"
    sig = hmac.new(b"a", body, hashlib.sha256).hexdigest()
    assert is_valid_signature_for_string_body(body, sig, "b") is False


def test_malformed_signature_rejected() -> None:
    body = b"{}"
    key = "x"
    assert is_valid_signature_for_string_body(body, "not-hex", key) is False
    assert is_valid_signature_for_string_body(body, "", key) is False
    assert is_valid_signature_for_string_body(body, "ab", key) is False


def test_empty_signing_key_rejected() -> None:
    body = b"{}"
    assert is_valid_signature_for_string_body(body, "00" * 32, "") is False
