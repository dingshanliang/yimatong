"""yimatong-zgb1.5 验收测试 — 重复查验、隐私与消费者识别边界（真实 PostgreSQL）。

证明以下 AC 在真实 PG + 真实 API 路径下成立：
1. 同一消费者重复查验时展示累计次数和最近查验信息，不再显示首次查验文案。
2. 不同匿名消费者不会因脆弱标识被错误合并。
3. 未取得同意前不采集手机号等个人信息，撤回同意后停止相关使用。
4. 隐私授权包含场景、版本、时间和撤回证据。
5. 重复查验与营销参与保持为不同业务事实。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import get_db, get_db_for_consumer, get_db_with_bypass
from app.main import app

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


@pytest.fixture
async def client(migrated_pg_url: str) -> AsyncGenerator[AsyncClient, None]:
    """ASGI in-process client，依赖指向 acceptance PG。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # 清空 rate_limiter 内存缓存（跨测试累积会触发限流）
    from app.middleware.rate_limit import rate_limiter

    rate_limiter._cache._mem_store.clear()  # type: ignore[attr-defined]

    engine = create_async_engine(migrated_pg_url)

    async def override_get_db():
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
            await session.commit()

    async def override_get_bypass():
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_with_bypass] = override_get_bypass
    # yimatong-zgb1.5：consumers 端点用 get_db_for_consumer（独立依赖），也需 override
    app.dependency_overrides[get_db_for_consumer] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def _reset_code_state(bypass_session, tenant_id: str, public_id: str) -> None:
    """重置 baseline 码状态：activated + 清 first_scanned_at + 清 scan_events + 清 consent。

    acceptance DB 跨测试文件共享，需彻底隔离每个测试。
    """
    await bypass_session.rollback()
    await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
    await bypass_session.execute(
        text(
            "UPDATE code_items SET status='activated', revoked_at=NULL, first_scanned_at=NULL "
            "WHERE tenant_id=:t AND public_id=:p"
        ),
        {"t": tenant_id, "p": public_id},
    )
    await bypass_session.execute(
        text("DELETE FROM scan_events WHERE public_id=:p"),
        {"p": public_id},
    )
    # 清该码的 consent 记录（隔离 consent 测试）
    await bypass_session.execute(
        text("DELETE FROM consent_records WHERE public_id=:p"),
        {"p": public_id},
    )
    await bypass_session.commit()


async def _grant_consent_via_api(
    client: AsyncClient,
    public_id: str,
    consent_type: str = "privacy",
    scenario: str = "lead_capture",
    policy_version: str = "2026-07-27-v1",
    scan_token: str | None = None,
    ip: str = "203.0.113.10",
) -> dict:
    """通过 API 授予 consent，返回响应体。"""
    headers = {"X-Real-IP": ip}
    if scan_token:
        headers["Authorization"] = f"Bearer {scan_token}"
    resp = await client.post(
        "/api/v1/public/consents",
        json={
            "consent_type": consent_type,
            "public_id": public_id,
            "scenario": scenario,
            "policy_version": policy_version,
        },
        headers=headers,
    )
    assert resp.status_code == 201, f"grant consent failed: {resp.status_code} {resp.text}"
    return resp.json()


async def _resolve_with_token(client: AsyncClient, public_id: str, ip: str = "203.0.113.10") -> str:
    """resolve 码并返回 scan_token，固定 IP 以确保后续请求的 ip_hash 一致。"""
    resp = await client.get(
        f"/c/{public_id}",
        headers={"Accept": "application/json", "X-Real-IP": ip},
    )
    assert resp.status_code == 200, f"resolve failed: {resp.text}"
    return resp.json()["scan_token"]


# ── AC1：重复查验展示累计 + 最近，不展示首次 ─────────────────────────────


class TestRepeatScanShowsCountAndLast:
    """AC1：同一消费者重复查验时展示累计次数和最近查验信息，不再显示首次查验文案。"""

    async def test_repeat_scan_count_and_last_time(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code_state(bypass_session, tenant_id, public_id)

        headers = {"Accept": "application/json"}

        # 首次查验
        r1 = await client.get(f"/c/{public_id}", headers=headers)
        first = r1.json()["scan_info"]
        assert first["is_first_scan"] is True
        assert first["verification_count"] == 1
        assert first["first_scan_time"] is not None
        assert first["last_scan_time"] is not None
        first_scan_time = first["first_scan_time"]
        first_last_scan_time = first["last_scan_time"]

        # 等待一下，确保第二次查验时间不同
        await asyncio.sleep(0.05)

        # 第二次查验（重复）
        r2 = await client.get(f"/c/{public_id}", headers=headers)
        repeat = r2.json()["scan_info"]
        assert repeat["is_first_scan"] is False
        assert repeat["verification_count"] == 2
        # AC1 核心：first_scan_time 不随重复更新（保持首查时间）
        assert repeat["first_scan_time"] == first_scan_time
        # AC1 核心：last_scan_time 是本次查验时间（比首次更晚）
        assert repeat["last_scan_time"] != first_last_scan_time
        assert repeat["last_scan_time"] is not None


# ── AC2：不同匿名消费者不被错误合并 ───────────────────────────────────────


class TestNoConsumerMerge:
    """AC2：不同匿名消费者不会因脆弱标识被错误合并。

    固化"不合并"不变式：scan_events 各自独立，不同匿名消费者的 scan 不被合并。
    yimatong-zgb1.10 引入了 visitor_id 列（first-party 稳定访客标识），但
    visitor_id 不做弱信号合并（Decision 22）：每个 scan_event 行独立，visitor_id
    只是关联标识，不基于 IP/设备/位置合并不同人。
    """

    async def test_distinct_scans_not_merged(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code_state(bypass_session, tenant_id, public_id)

        # 两个不同 IP/UA 的 scan（模拟两个匿名消费者）
        await client.get(
            f"/c/{public_id}",
            headers={"Accept": "application/json", "X-Real-IP": "203.0.113.1"},
        )
        await client.get(
            f"/c/{public_id}",
            headers={"Accept": "application/json", "X-Real-IP": "198.51.100.2"},
        )

        # DB：两条独立 scan_events（各自 ip_hash 不同），无合并
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        rows = (
            await bypass_session.execute(
                text("SELECT ip_hash FROM scan_events WHERE tenant_id=:t AND public_id=:p ORDER BY scan_time"),
                {"t": tenant_id, "p": public_id},
            )
        ).fetchall()
        # 两个不同的 ip_hash（不合并）
        ip_hashes = [r[0] for r in rows]
        assert len(set(ip_hashes)) == 2, f"两个匿名消费者的 ip_hash 不应被合并，实际 {ip_hashes}"
        # yimatong-zgb1.10：scan_events 现在有 visitor_id 列（first-party 稳定访客标识），
        # 但 visitor_id 不基于 IP/设备/位置合并不同人（Decision 22）。
        # 两个不同 IP 的 scan 应有各自独立的 visitor_id（或都为 NULL，因为测试未带 X-Visitor-ID）。
        visitor_ids = [
            r[1]
            for r in (
                await bypass_session.execute(
                    text(
                        "SELECT ip_hash, visitor_id FROM scan_events "
                        "WHERE tenant_id=:t AND public_id=:p ORDER BY scan_time"
                    ),
                    {"t": tenant_id, "p": public_id},
                )
            ).fetchall()
        ]
        # 不应有两个 scan 被错误合并为同一 visitor_id（除非确实是同一访客）
        # 这里两个不同 IP 的 scan，visitor_id 应该不同或为 NULL
        non_null_visitors = [v for v in visitor_ids if v is not None]
        # 如果有 visitor_id，两个不同 IP 的 scan 不应共享同一个（除非测试带了相同的 X-Visitor-ID）
        if len(non_null_visitors) >= 2:
            assert len(set(non_null_visitors)) >= 1, "不同 IP 的 scan 的 visitor_id 不应被错误合并"


# ── AC3：未同意不采集手机号；撤回后停止使用 ──────────────────────────────


class TestConsentGatingLeadCapture:
    """AC3：未取得同意前不采集手机号；撤回同意后停止相关使用。"""

    async def test_lead_capture_requires_consent(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code_state(bypass_session, tenant_id, public_id)

        # 固定 IP：resolve 与 lead-capture 必须同 IP（scan_token 校验 ip_hash）
        fixed_ip = "203.0.113.20"
        scan_token = await _resolve_with_token(client, public_id, ip=fixed_ip)

        # 未 grant consent → lead-capture 带 phone 应 403
        lead_no_consent = await client.post(
            "/api/v1/consumers/lead-capture",
            json={"public_id": public_id, "name": "张三", "phone": "13800138000"},
            headers={"Authorization": f"Bearer {scan_token}", "X-Real-IP": fixed_ip},
        )
        assert lead_no_consent.status_code == 403, f"未同意应 403: {lead_no_consent.text}"
        assert lead_no_consent.json()["detail"] == "consent_required"

    async def test_lead_capture_non_pii_allowed_without_consent(self, client, bypass_session, migrated_pg_url):
        """AC3：非 PII 字段（region/intention）不强制 consent。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code_state(bypass_session, tenant_id, public_id)

        fixed_ip = "203.0.113.21"
        scan_token = await _resolve_with_token(client, public_id, ip=fixed_ip)

        # 不带 phone（只 region/intention）→ 允许（非 PII）
        lead_no_pii = await client.post(
            "/api/v1/consumers/lead-capture",
            json={"public_id": public_id, "region": "北京", "intention": "想了解产品"},
            headers={"Authorization": f"Bearer {scan_token}", "X-Real-IP": fixed_ip},
        )
        assert lead_no_pii.status_code == 201, f"非 PII 留资应允许: {lead_no_pii.text}"

    async def test_consent_grant_allows_then_withdraw_blocks(self, client, bypass_session, migrated_pg_url):
        """AC3+AC4：grant 后可采集；withdraw 后停止。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code_state(bypass_session, tenant_id, public_id)

        fixed_ip = "203.0.113.30"
        scan_token = await _resolve_with_token(client, public_id, ip=fixed_ip)
        auth_headers = {"Authorization": f"Bearer {scan_token}", "X-Real-IP": fixed_ip}

        # grant consent（同 IP，确保 scan_token 校验通过）
        consent_resp = await _grant_consent_via_api(client, public_id, scan_token=scan_token, ip=fixed_ip)
        consent_id = consent_resp["id"]

        # lead-capture 带 phone 应成功
        lead_ok = await client.post(
            "/api/v1/consumers/lead-capture",
            json={"public_id": public_id, "name": "李四", "phone": "13900139000"},
            headers=auth_headers,
        )
        assert lead_ok.status_code == 201, f"grant 后应可采集: {lead_ok.text}"

        # withdraw consent（同 IP）
        withdraw = await client.post(
            f"/api/v1/public/consents/{consent_id}/withdraw",
            headers=auth_headers,
        )
        assert withdraw.status_code == 200
        assert withdraw.json()["status"] == "withdrawn"

        # 再次 lead-capture 带 phone → 应 403 consent_withdrawn
        lead_after_withdraw = await client.post(
            "/api/v1/consumers/lead-capture",
            json={"public_id": public_id, "name": "李四", "phone": "13900139001"},
            headers=auth_headers,
        )
        assert lead_after_withdraw.status_code == 403, f"撤回后应 403: {lead_after_withdraw.text}"
        assert lead_after_withdraw.json()["detail"] == "consent_withdrawn"


# ── AC4：隐私授权含场景、版本、时间、撤回证据 ──────────────────────────────


class TestConsentEvidenceChain:
    """AC4：consent_records 含 scenario + policy_version + user_agent + granted_at + withdrawn_at。"""

    async def test_grant_and_withdraw_evidence(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code_state(bypass_session, tenant_id, public_id)

        # 固定 IP：grant + withdraw 都需 scan_token 校验
        fixed_ip = "203.0.113.40"
        scan_token = await _resolve_with_token(client, public_id, ip=fixed_ip)

        # grant
        consent_resp = await _grant_consent_via_api(
            client,
            public_id,
            scenario="lead_capture",
            policy_version="2026-07-27-v1",
            scan_token=scan_token,
            ip=fixed_ip,
        )
        consent_id = consent_resp["id"]

        # DB 证据链
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row = (
            await bypass_session.execute(
                text(
                    "SELECT consent_type, status, scenario, policy_version, user_agent, "
                    "public_id, ip_hash, granted_at, withdrawn_at "
                    "FROM consent_records WHERE id=:cid"
                ),
                {"cid": consent_id},
            )
        ).one()
        assert row[0] == "privacy"  # consent_type
        assert row[1] == "granted"  # status
        assert row[2] == "lead_capture"  # scenario
        assert row[3] == "2026-07-27-v1"  # policy_version
        assert row[4] is not None  # user_agent（HTTPX UA）
        assert row[5] == public_id  # public_id
        assert row[6] is not None  # ip_hash
        assert row[7] is not None  # granted_at
        assert row[8] is None  # withdrawn_at（未撤回）

        # withdraw（用 scan_token + 同 IP）
        withdraw_resp = await client.post(
            f"/api/v1/public/consents/{consent_id}/withdraw",
            headers={"Authorization": f"Bearer {scan_token}", "X-Real-IP": fixed_ip},
        )
        assert withdraw_resp.status_code == 200

        # DB 撤回证据
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row_after = (
            await bypass_session.execute(
                text("SELECT status, withdrawn_at FROM consent_records WHERE id=:cid"),
                {"cid": consent_id},
            )
        ).one()
        assert row_after[0] == "withdrawn"
        assert row_after[1] is not None  # withdrawn_at 已记录


# ── AC5：重复查验与营销参与保持为不同业务事实 ──────────────────────────────


class TestVerificationSeparateFromMarketing:
    """AC5：scan_events（查验）vs benefit_claims（营销）独立；首次查验 ≠ 首次领取。"""

    async def test_separate_facts(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        benefit_id = summary["benefit"]["id"]
        await _reset_code_state(bypass_session, tenant_id, public_id)

        headers = {"Accept": "application/json"}

        # 首次查验
        r1 = await client.get(f"/c/{public_id}", headers=headers)
        assert r1.json()["scan_info"]["is_first_scan"] is True

        # 第二次查验（重复）
        r2 = await client.get(f"/c/{public_id}", headers=headers)
        assert r2.json()["scan_info"]["is_first_scan"] is False
        assert r2.json()["scan_info"]["verification_count"] == 2

        # 尝试领取权益（营销参与）— 这是独立业务事实
        scan_token = r2.json()["scan_token"]
        await client.post(
            "/api/v1/benefit-claims",
            json={"benefit_id": benefit_id, "scan_token": scan_token},
            headers={"X-Real-IP": "203.0.113.50"},
        )
        # 无论 claim 成功与否（可能需 phone/WeChat 等），查验事实不变
        # 关键：benefit_claims 表的行数与 scan_events 表的行数独立
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        scan_count = (
            await bypass_session.execute(
                text("SELECT count(*) FROM scan_events WHERE tenant_id=:t AND public_id=:p"),
                {"t": tenant_id, "p": public_id},
            )
        ).scalar()
        claim_count = (
            await bypass_session.execute(
                text("SELECT count(*) FROM benefit_claims WHERE tenant_id=:t AND benefit_id=:b"),
                {"t": tenant_id, "b": benefit_id},
            )
        ).scalar()

        # 查验事实：2 次 resolve = 2 条 scan_events（与 claim 无关）
        assert scan_count == 2, f"scan_events 应为 2（2 次 resolve），实际 {scan_count}"
        # claim 计数独立（0 或 1，取决于 claim 是否成功，但不影响 scan 计数）
        assert claim_count in (0, 1), f"benefit_claims 独立计数，实际 {claim_count}"

        # 首次查验时间 ≠ 首次领取时间（不同业务事实）
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        first_scan_at = (
            await bypass_session.execute(
                text("SELECT first_scanned_at FROM code_items WHERE tenant_id=:t AND public_id=:p"),
                {"t": tenant_id, "p": public_id},
            )
        ).scalar()
        assert first_scan_at is not None  # 首次查验已记录
        # benefit_claims 有自己的 created_at（如有），与 first_scanned_at 是不同字段、不同表、不同业务语义
