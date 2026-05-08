"""Policy outcomes for swaps and transaction risk evaluation."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PolicyDecisionStatus(StrEnum):
    """Possible policy outcomes."""

    ALLOWED = "allowed"
    NEEDS_APPROVAL = "needs_approval"
    REJECTED = "rejected"


class PolicyDecision(BaseModel):
    """Allow, needs-human-approval, or reject verdict from swap or transaction policy."""

    model_config = ConfigDict(frozen=True)

    status: PolicyDecisionStatus
    reason: str = Field(min_length=1)
