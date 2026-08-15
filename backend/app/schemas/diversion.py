"""Strict contracts for diversion investigation workflows."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

DiversionInvestigationStatus = Literal[
    "open",
    "pending_evidence",
    "confirmed_diversion",
    "false_positive",
    "normal_transfer",
]
DiversionEvidenceType = Literal["transfer", "order", "logistics", "explanation", "other"]
DiversionEvidenceSource = Literal["distributor", "brand_ops", "system", "other"]


class DiversionMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DiversionTransitionRequest(DiversionMutationRequest):
    expected_version: int = Field(ge=1)
    to_status: DiversionInvestigationStatus
    reason: str = Field(min_length=1, max_length=500)
    resolution_note: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def require_terminal_conclusion(self):
        terminal = {"confirmed_diversion", "false_positive", "normal_transfer"}
        if self.to_status in terminal:
            if self.resolution_note is None:
                raise ValueError("A terminal investigation requires a supporting note")
        elif self.resolution_note is not None:
            raise ValueError("Non-terminal transitions cannot replace the prior conclusion")
        return self


class DiversionEvidenceCreate(DiversionMutationRequest):
    expected_version: int = Field(ge=1)
    evidence_type: DiversionEvidenceType
    file_url: HttpUrl | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    evidence_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_evidence_content(self):
        if self.file_url is None and self.description is None:
            raise ValueError("Evidence requires a file URL or description")
        if self.file_url is not None and self.evidence_digest is None:
            raise ValueError("File evidence requires its SHA-256 digest")
        if self.file_url is None and self.evidence_digest is not None:
            raise ValueError("A digest is valid only for file evidence")
        return self


class DiversionMutationResult(BaseModel):
    resource_id: uuid.UUID
    clue_id: uuid.UUID
    clue_version: int
    investigation_status: DiversionInvestigationStatus
    resolved: bool
    observation_count: int
    replayed: bool
    actor_id: uuid.UUID | None
    recorded_at: str
