"""回访凭证：仅对单笔 claim 有效、带时效、绑定领取者主体。"""

import uuid

import jwt

from app.services.claim_revisit_credential import (
    REVISIT_CREDENTIAL_TTL_SECONDS,
    issue_revisit_credential,
    verify_revisit_credential,
)


def _subject():
    tenant_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    consumer_id = f"anon:v1:{uuid.uuid4().hex}"
    return tenant_id, claim_id, consumer_id


class TestIssueAndVerify:
    def test_roundtrip_returns_bound_subject(self):
        tenant_id, claim_id, consumer_id = _subject()

        token = issue_revisit_credential(tenant_id, claim_id, consumer_id)
        payload = verify_revisit_credential(token, expected_claim_id=claim_id, expected_tenant_id=tenant_id)

        assert payload is not None
        assert payload["claim_id"] == str(claim_id)
        assert payload["consumer_id"] == consumer_id

    def test_credential_only_valid_for_its_own_claim(self):
        tenant_id, claim_id, consumer_id = _subject()
        token = issue_revisit_credential(tenant_id, claim_id, consumer_id)

        assert verify_revisit_credential(token, expected_claim_id=uuid.uuid4(), expected_tenant_id=tenant_id) is None

    def test_credential_only_valid_for_its_own_tenant(self):
        tenant_id, claim_id, consumer_id = _subject()
        token = issue_revisit_credential(tenant_id, claim_id, consumer_id)

        assert verify_revisit_credential(token, expected_claim_id=claim_id, expected_tenant_id=uuid.uuid4()) is None


class TestExpiryAndTamper:
    def test_expired_credential_is_rejected(self):
        tenant_id, claim_id, consumer_id = _subject()
        token = issue_revisit_credential(tenant_id, claim_id, consumer_id, expires_in=-1)

        assert verify_revisit_credential(token, expected_claim_id=claim_id, expected_tenant_id=tenant_id) is None

    def test_tampered_credential_is_rejected(self):
        tenant_id, claim_id, consumer_id = _subject()
        token = issue_revisit_credential(tenant_id, claim_id, consumer_id)
        payload = jwt.decode(token, options={"verify_signature": False})
        payload["claim_id"] = str(uuid.uuid4())
        forged = jwt.encode(payload, "wrong-secret", algorithm="HS256")

        assert verify_revisit_credential(forged, expected_claim_id=claim_id, expected_tenant_id=tenant_id) is None

    def test_scan_token_is_not_a_revisit_credential(self):
        from app.services.scan_token import create_scan_token

        tenant_id, claim_id, consumer_id = _subject()
        scan_token = create_scan_token(
            public_id="pk",
            ip_hash=None,
            tenant_id=str(tenant_id),
            consumer_id=consumer_id,
        )

        assert verify_revisit_credential(scan_token, expected_claim_id=claim_id, expected_tenant_id=tenant_id) is None

    def test_default_ttl_is_multi_day_for_revisit_window(self):
        assert REVISIT_CREDENTIAL_TTL_SECONDS >= 5 * 24 * 3600

    def test_jti_is_unique_per_issuance(self):
        tenant_id, claim_id, consumer_id = _subject()

        first = issue_revisit_credential(tenant_id, claim_id, consumer_id)
        second = issue_revisit_credential(tenant_id, claim_id, consumer_id)

        left = jwt.decode(first, options={"verify_signature": False})
        right = jwt.decode(second, options={"verify_signature": False})
        assert left["jti"] != right["jti"]


# 领取成功 payload 的行为（凭证签发 + 幂等重放真实三态）已迁移至
# tests/test_services/test_claim_success_payload.py（service 接缝）。
