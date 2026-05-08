import json
import logging

import pytest
from fastapi.testclient import TestClient
from mercury.config import MercurySettings
from mercury.service import create_app


def _service_events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    events: list[dict] = []
    for record in caplog.records:
        if record.name != "mercury.service":
            continue
        msg = record.getMessage()
        for candidate in (
            msg,
            msg.rsplit(" | ", 1)[-1] if " | " in msg else "",
        ):
            if not candidate:
                continue
            try:
                events.append(json.loads(candidate))
            except json.JSONDecodeError:
                continue
            else:
                break
    return events


def test_http_logging_middleware_logs_request_and_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="mercury.service")
    app = create_app(settings=MercurySettings(app_name="Mercury Test"))
    client = TestClient(app)

    response = client.get("/healthz", headers={"X-Request-ID": "req-test-1"})

    assert response.status_code == 200
    events = _service_events(caplog)
    http_requests = [e for e in events if e.get("event") == "http_request"]
    http_responses = [e for e in events if e.get("event") == "http_response"]
    assert len(http_requests) == 1
    assert len(http_responses) == 1
    assert http_requests[0]["method"] == "GET"
    assert http_requests[0]["path"] == "/healthz"
    assert http_requests[0]["request_id"] == "req-test-1"
    assert http_responses[0]["status_code"] == 200
    assert http_responses[0]["request_id"] == "req-test-1"
    assert http_responses[0]["response_body_logged"] is True
    assert http_responses[0]["response_body"] == {
        "status": "ok",
        "service": "Mercury Test",
    }
