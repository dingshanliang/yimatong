"""防窜货自动比对服务：扫码时检测实际区域与预期区域是否一致"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import CodeAllocation, DiversionClue, Region

logger = logging.getLogger(__name__)


async def check_diversion(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    code_batch_id: uuid.UUID,
    public_id: str,
    code_item_id: uuid.UUID,
    detected_city: str | None = None,
    ip_hash: str | None = None,
) -> DiversionClue | None:
    """检查扫码地与预期区域是否一致，不一致则创建窜货线索。

    Args:
        db: 数据库会话
        tenant_id: 租户 ID
        code_batch_id: 码批次 ID
        public_id: 码对外 ID
        code_item_id: 码项 ID
        detected_city: 检测到的城市名（来自 IP 或地理位置）
        ip_hash: 扫码 IP 哈希

    Returns:
        如果检测到窜货嫌疑，返回新创建的 DiversionClue；否则返回 None。
    """
    if not detected_city:
        return None

    # 查询该码批次的分配记录，获取预期区域
    alloc_result = await db.execute(
        select(CodeAllocation).where(
            CodeAllocation.tenant_id == tenant_id,
            CodeAllocation.batch_id == code_batch_id,
        )
    )
    allocations = list(alloc_result.scalars().all())
    if not allocations:
        # 未分配区域，无法比对
        return None

    # 获取所有关联的预期区域
    expected_region_ids = [a.region_id for a in allocations if a.region_id]
    if not expected_region_ids:
        # 分配了但没有绑定区域
        return None

    # 查询预期区域详情
    regions_result = await db.execute(
        select(Region).where(
            Region.tenant_id == tenant_id,
            Region.id.in_(expected_region_ids),
        )
    )
    regions = list(regions_result.scalars().all())
    if not regions:
        return None

    # 检查检测到的城市是否在任一预期区域内
    matched = False
    matched_region_id: uuid.UUID | None = None
    matched_distributor_id: uuid.UUID | None = None

    for region in regions:
        if _city_matches_region(detected_city, region):
            matched = True
            matched_region_id = region.id
            # 找到对应的分配记录
            for alloc in allocations:
                if alloc.region_id == region.id:
                    matched_distributor_id = alloc.distributor_id
                    break
            break

    if matched:
        return None

    # 不匹配，创建窜货线索
    # 使用第一个分配的区域作为预期区域描述
    expected_region_name = ", ".join(
        f"{r.province or ''}{r.city or ''}".strip() or r.name for r in regions
    )

    clue = DiversionClue(
        tenant_id=tenant_id,
        public_id=public_id,
        code_item_id=code_item_id,
        expected_region=expected_region_name,
        detected_city=detected_city,
        distributor_id=matched_distributor_id,
        region_id=matched_region_id,
        ip_hash=ip_hash,
        resolved=False,
    )
    db.add(clue)
    await db.flush()

    logger.info(
        "diversion_clue_created tenant=%s public_id=%s expected=%s detected=%s",
        tenant_id,
        public_id,
        expected_region_name,
        detected_city,
    )
    return clue


def _city_matches_region(city: str, region: Region) -> bool:
    """检查城市是否匹配区域的覆盖范围。

    匹配逻辑：
    1. 如果区域有 coverage_areas（JSON），检查城市是否在其中
    2. 如果区域有 province/city，直接比较
    3. 如果区域只有 name，做模糊匹配
    """
    city_lower = city.lower().strip()

    # 检查 coverage_areas
    if region.coverage_areas:
        for area in region.coverage_areas:
            area_city = area.get("city", "") if isinstance(area, dict) else str(area)
            if area_city and city_lower in area_city.lower():
                return True

    # 检查 province/city
    if region.city and city_lower in region.city.lower():
        return True
    if region.province and city_lower in region.province.lower():
        return True

    # 模糊匹配 name
    if region.name and city_lower in region.name.lower():
        return True

    return False
