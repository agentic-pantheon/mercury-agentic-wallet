"""Integration tests for ``POST /v1/webhooks/alchemy/address-activity``."""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from mercury.config import MercurySettings
from mercury.service.api import create_app
from mercury.webhooks.alchemy_handler import ADDRESS_ACTIVITY
from tests.test_alchemy_webhook_handler import _ACTIVITY_ROW


def test_alchemy_webhook_missing_signature_401() -> None:
    app = create_app(settings=MercurySettings())
    app.state.alchemy_webhook_signing_key = "k"
    client = TestClient(app)
    response = client.post(
        "/v1/webhooks/alchemy/address-activity",
        content=b"{}",
    )
    assert response.status_code == 401


def test_alchemy_webhook_invalid_signature_403() -> None:
    app = create_app(settings=MercurySettings())
    app.state.alchemy_webhook_signing_key = "k"
    client = TestClient(app)
    body = json.dumps({"type": ADDRESS_ACTIVITY}).encode()
    response = client.post(
        "/v1/webhooks/alchemy/address-activity",
        content=body,
        headers={"X-Alchemy-Signature": "a" * 64},
    )
    assert response.status_code == 403


def test_alchemy_webhook_valid_signature_200() -> None:
    app = create_app(settings=MercurySettings())
    app.state.alchemy_webhook_signing_key = "whsec_test"
    client = TestClient(app)
    payload = {
        "webhookId": "wh_x",
        "id": "ev_x",
        "type": ADDRESS_ACTIVITY,
        "event": {"network": "ETH_MAINNET", "activity": [_ACTIVITY_ROW]},
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    sig = hmac.new(b"whsec_test", raw, hashlib.sha256).hexdigest()
    response = client.post(
        "/v1/webhooks/alchemy/address-activity",
        content=raw,
        headers={"X-Alchemy-Signature": sig},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["received"] is True
    assert data["skipped"] is False
    assert data["processed"] == 1
    assert data["normalized_count"] == 1
    assert len(data["alerts"]) == 1
