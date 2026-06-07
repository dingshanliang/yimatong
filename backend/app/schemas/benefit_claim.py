"""权益领取 Schema"""

from pydantic import BaseModel


class BenefitClaimRequest(BaseModel):
    benefit_id: str
    scan_token: str | None = None
    phone: str | None = None
