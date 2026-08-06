from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


InteractionStatus = Literal["ready", "planned", "disabled"]
AvailabilityStatus = Literal[
    "available",
    "needs_configuration",
    "verification_required",
    "upstream_unavailable",
    "disabled",
]
VerificationStatus = Literal[
    "verified",
    "contract_verified",
    "manual_required",
    "failed",
    "not_applicable",
]
SupportLevel = Literal["native", "converted", "combined", "fallback"]


class OperationReadiness(BaseModel):
    operation: str
    interaction_status: InteractionStatus
    availability_status: AvailabilityStatus
    verification_status: VerificationStatus
    support_level: SupportLevel = "native"
    status_reason: str | None = None
