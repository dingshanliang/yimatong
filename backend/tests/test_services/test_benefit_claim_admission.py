import inspect
import re
import uuid

import jwt

from app.core.config import settings
from app.services.benefit_claim_admission import build_claim_consumer_id, build_claim_idempotency_key
from app.services.scan_token import create_scan_token, verify_scan_token


def test_h5_claim_request_never_pays_or_uses_redis_as_claim_authority():
    from app.api.v1.benefit_claims import claim_benefit_h5

    source = inspect.getsource(claim_benefit_h5)
    assert "claim_red_packet" not in source
    assert "_claim_cache" not in source
    assert "claim_benefit(" in source


def test_authoritative_claim_replay_is_returned_as_success():
    from app.api.v1.benefit_claims import _is_successful_claim_outcome

    assert _is_successful_claim_outcome("replayed") is True
    assert _is_successful_claim_outcome("success") is True
    assert _is_successful_claim_outcome("risk_paused") is False


def test_claim_idempotency_key_is_token_unique_and_opaque():
    tenant_id = str(uuid.uuid4())
    benefit_id = uuid.uuid4()
    scan_event_id = str(uuid.uuid4())
    first_token = create_scan_token("CODE-001", "ip-hash", tenant_id, scan_event_id=scan_event_id)
    second_token = create_scan_token("CODE-001", "ip-hash", tenant_id, scan_event_id=scan_event_id)
    first_payload = verify_scan_token(first_token)
    second_payload = verify_scan_token(second_token)

    first_key = build_claim_idempotency_key(first_payload, benefit_id)
    second_key = build_claim_idempotency_key(second_payload, benefit_id)

    assert first_key != second_key
    assert re.fullmatch(r"claim:v1:[0-9a-f]{64}", first_key)
    assert first_token not in first_key
    assert "CODE-001" not in first_key
    assert tenant_id not in first_key


def test_claim_idempotency_key_rejects_missing_authority_claims():
    token = create_scan_token("CODE-001", "ip-hash", str(uuid.uuid4()), scan_event_id=str(uuid.uuid4()))
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    benefit_id = uuid.uuid4()

    for field in ("tenant_id", "public_id", "jti", "scan_event_id"):
        malformed = dict(payload)
        malformed.pop(field)
        try:
            build_claim_idempotency_key(malformed, benefit_id)
        except ValueError as exc:
            assert field in str(exc)
        else:
            raise AssertionError(f"missing {field} must fail closed")


def test_anonymous_consumer_identity_is_stable_and_opaque():
    payload = {
        "tenant_id": str(uuid.uuid4()),
        "public_id": "CODE-001",
        "visitor_id": "visitor-private-value",
    }

    identity = build_claim_consumer_id(payload)

    assert re.fullmatch(r"anon:v1:[0-9a-f]{64}", identity)
    assert payload["public_id"] not in identity
    assert payload["visitor_id"] not in identity
