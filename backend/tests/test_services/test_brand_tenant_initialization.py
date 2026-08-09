import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.models.audit import PlatformAuditLog
from app.models.invite_code import InviteCodeStatus, TenantInviteCode
from app.models.plan import PlanDefinition
from app.models.platform_opening import PlatformTenantOpening
from app.models.tenant import Account, Organization, Permission, Role, Tenant, TenantStatus, account_roles
from app.modules.brand_tenant_initialization import (
    BrandTenantInitialization,
    ControlledInviteOpening,
    InitialAdminState,
    InitializeBrandTenant,
    PlatformOpening,
    TrustedAutomationOpening,
)
from app.modules.initial_admin_activation import InitialAdminActivation, InitialAdminNotPending
from app.services.auth import AuthError, confirm_password_reset
from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS
from app.utils.security import hash_password
from scripts.backfill_admin_permissions import backfill_empty_admin_permissions


def _command(opening) -> InitializeBrandTenant:
    return InitializeBrandTenant(
        name="青岭良仓",
        admin_name="张三",
        admin_email="owner@example.com",
        industry="食品饮料",
        opening=opening,
    )


class FakeResetCache:
    def __init__(self):
        self.values: dict[str, dict] = {}

    async def set(self, key: str, value: dict, ttl: int) -> None:
        self.values[key] = value

    set_shared = set

    async def set_shared_if_absent(self, key: str, value: dict, ttl: int) -> bool:
        if key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key: str) -> dict | None:
        return self.values.get(key)

    get_shared = get

    async def invalidate(self, key: str) -> None:
        self.values.pop(key, None)

    async def consume(self, key: str, expected: dict) -> bool:
        if self.values.get(key) != expected:
            return False
        self.values.pop(key, None)
        return True

    consume_shared = consume

    async def rate_limit_check(self, key: str, max_attempts: int, window_seconds: int):
        return True, max_attempts

    # b69b3563 把 confirm_password_reset 的限流改成跨 worker 共享的
    # rate_limit_check_shared；测试桩需要提供同名方法，否则激活流程的限流调用
    # 会抛 AttributeError。行为与 rate_limit_check 一致：单进程测试恒放行。
    rate_limit_check_shared = rate_limit_check


@pytest.mark.anyio
async def test_platform_initialization_materializes_complete_database_state(db):
    receipt = await BrandTenantInitialization(db).initialize(
        _command(PlatformOpening(operator_id="platform-admin", plan_name="starter"))
    )

    tenant = await db.get(Tenant, receipt.tenant_id)
    account = await db.get(Account, receipt.initial_admin_id)
    role = (
        await db.execute(select(Role).where(Role.tenant_id == receipt.tenant_id, Role.name == "admin"))
    ).scalar_one()
    await db.refresh(role, attribute_names=["permissions"])
    fixed_roles = list((await db.execute(select(Role).where(Role.tenant_id == receipt.tenant_id))).scalars().all())

    assert receipt.initial_admin_state is InitialAdminState.pending_activation
    assert account is not None and account.is_active is False
    assert tenant is not None
    assert tenant.plan.value == "starter"
    assert tenant.quota == {"max_codes": 10000, "max_campaigns": 10, "max_accounts": 5}
    assert tenant.enabled_features == {"ai_assistant": True}
    assert tenant.categories
    assert {permission.code for permission in role.permissions} == set(WEB_ROLE_PERMISSIONS["admin"])
    assert {item.name for item in fixed_roles} == {"admin", "operator", "viewer"}
    assert (
        await db.scalar(
            select(func.count())
            .select_from(PlatformAuditLog)
            .where(
                PlatformAuditLog.target_tenant_id == str(receipt.tenant_id),
                PlatformAuditLog.action == "brand_tenant_initialized",
            )
        )
        == 1
    )


@pytest.mark.anyio
async def test_plan_and_industry_values_are_copied_as_tenant_snapshot(db):
    plan = (await db.execute(select(PlanDefinition).where(PlanDefinition.name == "free"))).scalar_one()
    receipt = await BrandTenantInitialization(db).initialize(
        _command(
            TrustedAutomationOpening(
                actor="test",
                chosen_password="StrongPass123",
                stable_tenant_key="snapshot-tenant",
            )
        )
    )
    tenant = await db.get(Tenant, receipt.tenant_id)
    original_quota = dict(tenant.quota)
    original_categories = list(tenant.categories)

    plan.quota_defaults = {"max_codes": 1}
    tenant.industry = "其他行业"
    await db.flush()

    assert tenant.quota == original_quota
    assert tenant.categories == original_categories


@pytest.mark.anyio
async def test_controlled_invite_is_consumed_and_admin_is_active(db):
    invite = TenantInviteCode(
        code="INVITE-BRAND-1",
        tenant_type="brand",
        max_uses=1,
        used_count=0,
        status=InviteCodeStatus.active,
        expires_at=datetime.now(UTC) + timedelta(days=1),
        created_by_actor="platform-admin",
    )
    db.add(invite)
    await db.flush()

    receipt = await BrandTenantInitialization(db).initialize(
        _command(
            ControlledInviteOpening(
                invite_code=invite.code,
                chosen_password="StrongPass123",
            )
        )
    )
    account = await db.get(Account, receipt.initial_admin_id)
    audit = (
        await db.execute(
            select(PlatformAuditLog).where(
                PlatformAuditLog.target_tenant_id == str(receipt.tenant_id),
                PlatformAuditLog.action == "brand_tenant_initialized",
            )
        )
    ).scalar_one()

    assert receipt.initial_admin_state is InitialAdminState.active
    assert account is not None and account.is_active is True
    assert invite.used_count == 1
    assert invite.status is InviteCodeStatus.depleted
    assert audit.operator_id == str(invite.id)
    assert len(audit.operator_id) <= 36


@pytest.mark.anyio
async def test_audit_failure_rolls_back_every_initialized_record(db, monkeypatch):
    async def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(
        "app.modules.brand_tenant_initialization.service.write_audit_log",
        fail_audit,
    )
    tenant_key = f"rollback-{uuid.uuid4().hex[:8]}"

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await BrandTenantInitialization(db).initialize(
            _command(
                TrustedAutomationOpening(
                    actor="test",
                    chosen_password="StrongPass123",
                    stable_tenant_key=tenant_key,
                )
            )
        )
    await db.rollback()

    assert await db.scalar(select(func.count()).select_from(Tenant).where(Tenant.slug == tenant_key)) == 0
    assert await db.scalar(select(func.count()).select_from(Permission)) == 0


@pytest.mark.anyio
async def test_pending_admin_can_activate_and_reissued_link_invalidates_old_link(db):
    receipt = await BrandTenantInitialization(db).initialize(_command(PlatformOpening(operator_id="platform-admin")))
    db.add(
        PlatformTenantOpening(
            idempotency_key=f"test-{uuid.uuid4()}",
            request_hash="a" * 64,
            tenant_id=receipt.tenant_id,
            initial_admin_id=receipt.initial_admin_id,
            initial_admin_state="pending_activation",
        )
    )
    await db.commit()
    cache = FakeResetCache()
    activation = InitialAdminActivation(db, cache)

    first = await activation.issue_or_reissue(
        tenant_id=receipt.tenant_id,
        initial_admin_id=receipt.initial_admin_id,
        operator_id="platform-admin",
    )
    second = await activation.issue_or_reissue(
        tenant_id=receipt.tenant_id,
        operator_id="platform-admin",
    )
    first_token = first.url.split("token=", 1)[1].split("&", 1)[0]
    second_token = second.url.split("token=", 1)[1].split("&", 1)[0]

    with pytest.raises(AuthError, match="无效"):
        await confirm_password_reset(
            db=db,
            token=first_token,
            account_id_str=str(receipt.initial_admin_id),
            new_password="CustomerPass123",
            client_ip="127.0.0.1",
            cache=cache,
        )

    await confirm_password_reset(
        db=db,
        token=second_token,
        account_id_str=str(receipt.initial_admin_id),
        new_password="CustomerPass123",
        client_ip="127.0.0.1",
        cache=cache,
    )
    account = await db.get(Account, receipt.initial_admin_id)
    assert account is not None and account.is_active is True
    with pytest.raises(InitialAdminNotPending):
        await activation.issue_or_reissue(
            tenant_id=receipt.tenant_id,
            initial_admin_id=receipt.initial_admin_id,
            operator_id="platform-admin",
        )


@pytest.mark.anyio
async def test_cancelled_pending_activation_rejects_an_already_issued_token(db):
    receipt = await BrandTenantInitialization(db).initialize(_command(PlatformOpening(operator_id="platform-admin")))
    opening = PlatformTenantOpening(
        idempotency_key=f"test-{uuid.uuid4()}",
        request_hash="e" * 64,
        tenant_id=receipt.tenant_id,
        initial_admin_id=receipt.initial_admin_id,
        initial_admin_state="pending_activation",
    )
    db.add(opening)
    await db.commit()
    cache = FakeResetCache()
    activation = InitialAdminActivation(db, cache)
    ticket = await activation.issue_or_reissue(
        tenant_id=receipt.tenant_id,
        initial_admin_id=receipt.initial_admin_id,
        operator_id="platform-admin",
    )
    token = ticket.url.split("token=", 1)[1].split("&", 1)[0]

    tenant = await db.get(Tenant, receipt.tenant_id)
    tenant.status = TenantStatus.terminated
    assert await activation.cancel_pending(tenant_id=receipt.tenant_id) is True
    await db.commit()

    with pytest.raises(AuthError, match="激活状态已失效"):
        await confirm_password_reset(
            db=db,
            token=token,
            account_id_str=str(receipt.initial_admin_id),
            new_password="CustomerPass123",
            client_ip="127.0.0.1",
            cache=cache,
        )
    await db.refresh(opening)
    assert opening.initial_admin_state == "cancelled"


@pytest.mark.anyio
async def test_failed_activation_does_not_overwrite_newer_token(db):
    receipt = await BrandTenantInitialization(db).initialize(_command(PlatformOpening(operator_id="platform-admin")))
    db.add(
        PlatformTenantOpening(
            idempotency_key=f"test-{uuid.uuid4()}",
            request_hash="c" * 64,
            tenant_id=receipt.tenant_id,
            initial_admin_id=receipt.initial_admin_id,
            initial_admin_state="pending_activation",
        )
    )
    await db.commit()
    cache = FakeResetCache()
    ticket = await InitialAdminActivation(db, cache).issue_or_reissue(
        tenant_id=receipt.tenant_id,
        initial_admin_id=receipt.initial_admin_id,
        operator_id="platform-admin",
    )
    token = ticket.url.split("token=", 1)[1].split("&", 1)[0]
    reset_key = f"reset:{receipt.initial_admin_id}"
    newer_record = {"token_hash": "newer", "account_id": str(receipt.initial_admin_id)}

    async def install_newer_before_restore(key: str, value: dict, ttl: int) -> bool:
        cache.values[key] = newer_record
        return False

    cache.set_shared_if_absent = install_newer_before_restore
    with patch("app.services.audit.write_audit_log", side_effect=RuntimeError("audit unavailable")):
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await confirm_password_reset(
                db=db,
                token=token,
                account_id_str=str(receipt.initial_admin_id),
                new_password="CustomerPass123",
                client_ip="127.0.0.1",
                cache=cache,
            )
    assert cache.values[reset_key] == newer_record


@pytest.mark.anyio
async def test_activation_token_is_restored_when_password_transaction_fails(db):
    receipt = await BrandTenantInitialization(db).initialize(_command(PlatformOpening(operator_id="platform-admin")))
    db.add(
        PlatformTenantOpening(
            idempotency_key=f"test-{uuid.uuid4()}",
            request_hash="b" * 64,
            tenant_id=receipt.tenant_id,
            initial_admin_id=receipt.initial_admin_id,
            initial_admin_state="pending_activation",
        )
    )
    await db.commit()
    cache = FakeResetCache()
    ticket = await InitialAdminActivation(db, cache).issue_or_reissue(
        tenant_id=receipt.tenant_id,
        initial_admin_id=receipt.initial_admin_id,
        operator_id="platform-admin",
    )
    token = ticket.url.split("token=", 1)[1].split("&", 1)[0]

    with patch("app.services.audit.write_audit_log", side_effect=RuntimeError("audit unavailable")):
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await confirm_password_reset(
                db=db,
                token=token,
                account_id_str=str(receipt.initial_admin_id),
                new_password="CustomerPass123",
                client_ip="127.0.0.1",
                cache=cache,
            )

    await confirm_password_reset(
        db=db,
        token=token,
        account_id_str=str(receipt.initial_admin_id),
        new_password="CustomerPass123",
        client_ip="127.0.0.1",
        cache=cache,
    )
    account = await db.get(Account, receipt.initial_admin_id)
    assert account is not None and account.is_active is True


@pytest.mark.anyio
async def test_historical_backfill_only_fills_an_empty_admin_role(db):
    tenant = Tenant(name="历史租户", slug="historical-tenant")
    db.add(tenant)
    await db.flush()
    organization = Organization(tenant_id=tenant.id, name="历史租户默认组织")
    db.add(organization)
    await db.flush()
    account = Account(
        tenant_id=tenant.id,
        organization_id=organization.id,
        email="history@example.com",
        hashed_password=hash_password("HistoryPass123"),
        name="历史管理员",
    )
    role = Role(tenant_id=tenant.id, name="admin")
    db.add_all([account, role])
    await db.flush()
    await db.execute(account_roles.insert().values(account_id=account.id, role_id=role.id))

    report = await backfill_empty_admin_permissions(db)
    await db.refresh(role, attribute_names=["permissions"])

    assert report["repaired"] == [str(tenant.id)]
    assert {permission.code for permission in role.permissions} == set(WEB_ROLE_PERMISSIONS["admin"])

    report_again = await backfill_empty_admin_permissions(db)
    assert report_again["repaired"] == []
    assert report_again["skipped_custom"] == [str(tenant.id)]
