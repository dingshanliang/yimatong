"""Strict request contracts for tenant channel management."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ChannelSearchQuery = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]
ChannelEntityStatus = Literal["active", "inactive"]
ChannelDimension = Literal["distributor", "region", "store"]
DiversionSeverity = Literal["high", "medium", "low"]


class ChannelMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DistributorCreate(ChannelMutationRequest):
    name: str = Field(min_length=1, max_length=120)
    code: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    contact_name: str | None = Field(default=None, min_length=1, max_length=80)
    contact_phone: str | None = Field(default=None, pattern=r"^1\d{10}$")
    status: str = Field(default="active", pattern=r"^(active|inactive)$")


class DistributorUpdate(ChannelMutationRequest):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    contact_name: str | None = Field(default=None, min_length=1, max_length=80)
    contact_phone: str | None = Field(default=None, pattern=r"^1\d{10}$")
    status: str | None = Field(default=None, pattern=r"^(active|inactive)$")

    @model_validator(mode="after")
    def require_change(self):
        if not (self.model_fields_set - {"expected_version"}):
            raise ValueError("At least one field is required")
        return self


class RegionCoverageArea(ChannelMutationRequest):
    province: str | None = Field(default=None, min_length=1, max_length=40)
    city: str | None = Field(default=None, min_length=1, max_length=40)


class RegionCreate(ChannelMutationRequest):
    name: str = Field(min_length=1, max_length=120)
    code: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    province: str | None = Field(default=None, min_length=1, max_length=40)
    city: str | None = Field(default=None, min_length=1, max_length=40)
    coverage_type: str = Field(default="city", pattern=r"^(city|province|multi_province)$")
    coverage_areas: list[RegionCoverageArea] | None = Field(default=None, max_length=34)
    distributor_id: uuid.UUID | None = None
    status: str = Field(default="active", pattern=r"^(active|inactive)$")


class RegionUpdate(ChannelMutationRequest):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    province: str | None = Field(default=None, min_length=1, max_length=40)
    city: str | None = Field(default=None, min_length=1, max_length=40)
    coverage_type: str | None = Field(default=None, pattern=r"^(city|province|multi_province)$")
    coverage_areas: list[RegionCoverageArea] | None = Field(default=None, max_length=34)
    distributor_id: uuid.UUID | None = None
    status: str | None = Field(default=None, pattern=r"^(active|inactive)$")

    @model_validator(mode="after")
    def require_change(self):
        if not (self.model_fields_set - {"expected_version"}):
            raise ValueError("At least one field is required")
        return self


class StoreCreate(ChannelMutationRequest):
    name: str = Field(min_length=1, max_length=120)
    code: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    region_id: uuid.UUID | None = None
    distributor_id: uuid.UUID | None = None
    address: str | None = Field(default=None, min_length=1, max_length=500)
    status: str = Field(default="active", pattern=r"^(active|inactive)$")


class StoreUpdate(ChannelMutationRequest):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    region_id: uuid.UUID | None = None
    distributor_id: uuid.UUID | None = None
    address: str | None = Field(default=None, min_length=1, max_length=500)
    status: str | None = Field(default=None, pattern=r"^(active|inactive)$")

    @model_validator(mode="after")
    def require_change(self):
        if not (self.model_fields_set - {"expected_version"}):
            raise ValueError("At least one field is required")
        return self


class BatchAssign(ChannelMutationRequest):
    distributor_id: uuid.UUID | None = None
    region_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def require_exact_target(self):
        if (self.distributor_id is None) == (self.region_id is None):
            raise ValueError("Exactly one channel target is required")
        return self


class StoreAllocation(ChannelMutationRequest):
    batch_id: uuid.UUID
    target_type: str = Field(pattern=r"^(distributor|region|store)$")
    distributor_id: uuid.UUID | None = None
    region_id: uuid.UUID | None = None
    store_id: uuid.UUID | None = None
    quantity: int = Field(gt=0, le=10_000_000)
    reason: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_exact_target(self):
        targets = {
            "distributor": self.distributor_id,
            "region": self.region_id,
            "store": self.store_id,
        }
        if targets[self.target_type] is None or sum(value is not None for value in targets.values()) != 1:
            raise ValueError("Target id must match target_type exactly")
        return self


class AllocationReassign(ChannelMutationRequest):
    expected_version: int = Field(ge=1)
    target_type: str = Field(pattern=r"^(distributor|region|store)$")
    distributor_id: uuid.UUID | None = None
    region_id: uuid.UUID | None = None
    store_id: uuid.UUID | None = None
    quantity: int = Field(gt=0, le=10_000_000)
    reason: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_exact_target(self):
        targets = {
            "distributor": self.distributor_id,
            "region": self.region_id,
            "store": self.store_id,
        }
        if targets[self.target_type] is None or sum(value is not None for value in targets.values()) != 1:
            raise ValueError("Target id must match target_type exactly")
        return self


class AllocationArchive(ChannelMutationRequest):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=200)


class AccountScopeCreate(ChannelMutationRequest):
    account_id: uuid.UUID
    scope_type: str = Field(pattern=r"^(distributor|region|store)$")
    distributor_id: uuid.UUID | None = None
    region_id: uuid.UUID | None = None
    store_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def require_exact_scope(self):
        targets = {
            "distributor": self.distributor_id,
            "region": self.region_id,
            "store": self.store_id,
        }
        if targets[self.scope_type] is None or sum(value is not None for value in targets.values()) != 1:
            raise ValueError("Scope id must match scope_type exactly")
        return self
