"""Mercury-native HTTP request and response models."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mercury.custody import WalletIdValidationError, validate_wallet_id


class MercuryInvokeRequest(BaseModel):
    """Structured request envelope for invoking Mercury (in-process or tests)."""

    model_config = ConfigDict(extra="forbid")

    request_id: str | None = Field(default=None, min_length=1)
    user_id: str = Field(min_length=1)
    wallet_id: str = Field(min_length=1)
    intent: dict[str, Any] | str
    chain: str | None = Field(default=None, min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1)
    approval_response: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("wallet_id")
    @classmethod
    def validate_wallet(cls, value: str) -> str:
        """Reject wallet IDs that cannot be safely embedded in 1Claw paths."""

        try:
            return validate_wallet_id(value)
        except WalletIdValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("chain")
    @classmethod
    def normalize_chain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized or None

    def effective_request_id(self, header_request_id: str | None = None) -> str:
        """Return request ID from body, header, or a generated UUID."""

        return self.request_id or header_request_id or str(uuid4())

    def effective_idempotency_key(self, header_idempotency_key: str | None = None) -> str | None:
        """Return idempotency key from body or header."""

        return self.idempotency_key or header_idempotency_key


class MercuryError(BaseModel):
    """Sanitized error payload aligned with `MercuryErrorInfo` for API responses."""

    code: str
    category: str
    message: str
    retryable: bool = False
    recoverable: bool = True
    user_action: str | None = None
    llm_action: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MercuryInvokeResponse(BaseModel):
    """Structured response returned by Mercury invocation."""

    request_id: str
    status: str
    chain: str | None = None
    message: str
    data: dict[str, Any] | None = None
    tx_hash: str | None = None
    receipt: dict[str, Any] | None = None
    approval_required: bool = False
    approval_payload: dict[str, Any] | None = None
    error: MercuryError | None = None


class HealthResponse(BaseModel):
    """Health endpoint response."""

    status: str
    service: str


class ReadinessResponse(BaseModel):
    """Readiness endpoint response."""

    status: str
    service: str
    default_chain: str
    supported_chains: list[str]
