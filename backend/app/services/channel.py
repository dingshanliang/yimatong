"""渠道服务层：经销商/区域/门店 CRUD + 渠道流向登记 + 窜货检测"""

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import AccountChannelScope, CodeAllocation, Distributor, DiversionClue, Region, Store
from app.models.code import CodeBatch, CodeItem
from app.models.product import SKU, Product
from app.models.scan import ScanEvent
from app.utils import utcnow


def _like(value: str) -> str:
    return f"%{value.strip()}%"


def _dt(value) -> str | None:
    return value.isoformat() if value else None


def _diversion_severity(clue: DiversionClue) -> str:
    if not clue.detected_city or not clue.expected_region:
        return "medium"
    return "medium" if clue.detected_city in clue.expected_region else "high"


PROVINCE_CITY_MAP: dict[str, set[str]] = {
    "北京": {"北京"},
    "天津": {"天津"},
    "河北": {"石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德", "沧州", "廊坊", "衡水"},
    "山西": {"太原", "大同", "阳泉", "长治", "晋城", "朔州", "晋中", "运城", "忻州", "临汾", "吕梁"},
    "内蒙古": {
        "呼和浩特",
        "包头",
        "乌海",
        "赤峰",
        "通辽",
        "鄂尔多斯",
        "呼伦贝尔",
        "巴彦淖尔",
        "乌兰察布",
        "兴安盟",
        "锡林郭勒盟",
        "阿拉善盟",
    },
    "辽宁": {
        "沈阳",
        "大连",
        "鞍山",
        "抚顺",
        "本溪",
        "丹东",
        "锦州",
        "营口",
        "阜新",
        "辽阳",
        "盘锦",
        "铁岭",
        "朝阳",
        "葫芦岛",
    },
    "吉林": {"长春", "吉林", "四平", "辽源", "通化", "白山", "松原", "白城", "延边"},
    "黑龙江": {
        "哈尔滨",
        "齐齐哈尔",
        "鸡西",
        "鹤岗",
        "双鸭山",
        "大庆",
        "伊春",
        "佳木斯",
        "七台河",
        "牡丹江",
        "黑河",
        "绥化",
        "大兴安岭",
    },
    "上海": {"上海"},
    "江苏": {"南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁"},
    "浙江": {"杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水"},
    "安徽": {
        "合肥",
        "芜湖",
        "蚌埠",
        "淮南",
        "马鞍山",
        "淮北",
        "铜陵",
        "安庆",
        "黄山",
        "滁州",
        "阜阳",
        "宿州",
        "六安",
        "亳州",
        "池州",
        "宣城",
    },
    "福建": {"福州", "厦门", "莆田", "三明", "泉州", "漳州", "南平", "龙岩", "宁德"},
    "江西": {"南昌", "景德镇", "萍乡", "九江", "新余", "鹰潭", "赣州", "吉安", "宜春", "抚州", "上饶"},
    "山东": {
        "济南",
        "青岛",
        "淄博",
        "枣庄",
        "东营",
        "烟台",
        "潍坊",
        "济宁",
        "泰安",
        "威海",
        "日照",
        "临沂",
        "德州",
        "聊城",
        "滨州",
        "菏泽",
    },
    "河南": {
        "郑州",
        "开封",
        "洛阳",
        "平顶山",
        "安阳",
        "鹤壁",
        "新乡",
        "焦作",
        "濮阳",
        "许昌",
        "漯河",
        "三门峡",
        "南阳",
        "商丘",
        "信阳",
        "周口",
        "驻马店",
        "济源",
    },
    "湖北": {
        "武汉",
        "黄石",
        "十堰",
        "宜昌",
        "襄阳",
        "鄂州",
        "荆门",
        "孝感",
        "荆州",
        "黄冈",
        "咸宁",
        "随州",
        "恩施",
        "仙桃",
        "潜江",
        "天门",
        "神农架",
    },
    "湖南": {
        "长沙",
        "株洲",
        "湘潭",
        "衡阳",
        "邵阳",
        "岳阳",
        "常德",
        "张家界",
        "益阳",
        "郴州",
        "永州",
        "怀化",
        "娄底",
        "湘西",
    },
    "广东": {
        "广州",
        "韶关",
        "深圳",
        "珠海",
        "汕头",
        "佛山",
        "江门",
        "湛江",
        "茂名",
        "肇庆",
        "惠州",
        "梅州",
        "汕尾",
        "河源",
        "阳江",
        "清远",
        "东莞",
        "中山",
        "潮州",
        "揭阳",
        "云浮",
    },
    "广西": {
        "南宁",
        "柳州",
        "桂林",
        "梧州",
        "北海",
        "防城港",
        "钦州",
        "贵港",
        "玉林",
        "百色",
        "贺州",
        "河池",
        "来宾",
        "崇左",
    },
    "海南": {
        "海口",
        "三亚",
        "三沙",
        "儋州",
        "五指山",
        "琼海",
        "文昌",
        "万宁",
        "东方",
        "定安",
        "屯昌",
        "澄迈",
        "临高",
        "白沙",
        "昌江",
        "乐东",
        "陵水",
        "保亭",
        "琼中",
    },
    "重庆": {"重庆"},
    "四川": {
        "成都",
        "自贡",
        "攀枝花",
        "泸州",
        "德阳",
        "绵阳",
        "广元",
        "遂宁",
        "内江",
        "乐山",
        "南充",
        "眉山",
        "宜宾",
        "广安",
        "达州",
        "雅安",
        "巴中",
        "资阳",
        "阿坝",
        "甘孜",
        "凉山",
    },
    "贵州": {"贵阳", "六盘水", "遵义", "安顺", "毕节", "铜仁", "黔西南", "黔东南", "黔南"},
    "云南": {
        "昆明",
        "曲靖",
        "玉溪",
        "保山",
        "昭通",
        "丽江",
        "普洱",
        "临沧",
        "楚雄",
        "红河",
        "文山",
        "西双版纳",
        "大理",
        "德宏",
        "怒江",
        "迪庆",
    },
    "西藏": {"拉萨", "日喀则", "昌都", "林芝", "山南", "那曲", "阿里"},
    "陕西": {"西安", "铜川", "宝鸡", "咸阳", "渭南", "延安", "汉中", "榆林", "安康", "商洛"},
    "甘肃": {
        "兰州",
        "嘉峪关",
        "金昌",
        "白银",
        "天水",
        "武威",
        "张掖",
        "平凉",
        "酒泉",
        "庆阳",
        "定西",
        "陇南",
        "临夏",
        "甘南",
    },
    "青海": {"西宁", "海东", "海北", "黄南", "海南", "果洛", "玉树", "海西"},
    "宁夏": {"银川", "石嘴山", "吴忠", "固原", "中卫"},
    "新疆": {
        "乌鲁木齐",
        "克拉玛依",
        "吐鲁番",
        "哈密",
        "昌吉",
        "博尔塔拉",
        "巴音郭楞",
        "阿克苏",
        "克孜勒苏",
        "喀什",
        "和田",
        "伊犁",
        "塔城",
        "阿勒泰",
        "石河子",
        "阿拉尔",
        "图木舒克",
        "五家渠",
        "北屯",
        "铁门关",
        "双河",
        "可克达拉",
        "昆玉",
        "胡杨河",
        "新星",
        "白杨",
    },
    "香港": {"香港"},
    "澳门": {"澳门"},
    "台湾": {"台北", "新北", "桃园", "台中", "台南", "高雄", "基隆", "新竹", "嘉义"},
}


def normalize_region_coverage(
    coverage_type: str | None,
    province: str | None,
    city: str | None,
    coverage_areas: list[dict] | None,
) -> tuple[str, str | None, str | None, list[dict]]:
    normalized_type = coverage_type or "city"
    if normalized_type not in {"city", "province", "multi_province"}:
        raise ValueError("区域覆盖类型无效")

    normalized_province = province.strip() if province else None
    normalized_city = city.strip() if city else None

    if normalized_type == "city":
        if not normalized_city:
            if normalized_province:
                raise ValueError("城市片区必须选择城市")
            return normalized_type, None, None, []
        return (
            normalized_type,
            normalized_province,
            normalized_city,
            [{"province": normalized_province, "city": normalized_city}],
        )

    if normalized_type == "province":
        if not normalized_province:
            raise ValueError("省级片区必须选择省份")
        return normalized_type, normalized_province, None, [{"province": normalized_province, "city": None}]

    areas: list[dict] = []
    seen: set[str] = set()
    for area in coverage_areas or []:
        area_province = str(area.get("province") or "").strip()
        if not area_province or area_province in seen:
            continue
        seen.add(area_province)
        areas.append({"province": area_province, "city": None})
    if len(areas) < 2:
        raise ValueError("大区片区至少选择两个省份")
    return normalized_type, areas[0]["province"], None, areas


def region_coverage_label(region: Region) -> str:
    areas = region.coverage_areas or []
    labels = [str(area.get("city") or area.get("province") or "").strip() for area in areas]
    labels = [label for label in labels if label]
    if labels:
        return "、".join(labels)
    if region.city:
        return region.city
    return region.province or ""


def _region_matches_detected_city(region: Region, detected_city: str) -> bool:
    areas = region.coverage_areas or [{"province": region.province, "city": region.city}]
    for area in areas:
        province = str(area.get("province") or "").strip()
        city = str(area.get("city") or "").strip()
        if city and detected_city == city:
            return True
        if province and (detected_city == province or detected_city in PROVINCE_CITY_MAP.get(province, set())):
            return True
    return False


def _expected_region_label(region: Region) -> str:
    coverage_label = region_coverage_label(region)
    if region.coverage_type == "city":
        return coverage_label
    return f"{region.name}（{coverage_label}）" if coverage_label else region.name


async def generate_channel_code(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    model: type[Distributor] | type[Region] | type[Store],
    prefix: str,
) -> str:
    """Generate a tenant-scoped readable channel code for distributors, regions, and stores."""
    day_prefix = f"{prefix}-{utcnow().strftime('%Y%m%d')}"
    rows = (
        (
            await db.execute(
                select(model.code).where(
                    model.tenant_id == tenant_id,
                    model.code.like(f"{day_prefix}-%"),
                )
            )
        )
        .scalars()
        .all()
    )
    max_suffix = 0
    for code in rows:
        suffix = str(code).removeprefix(f"{day_prefix}-")
        if suffix.isdigit():
            max_suffix = max(max_suffix, int(suffix))
    return f"{day_prefix}-{max_suffix + 1:03d}"


async def _sum_allocated(db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID) -> int:
    total = (
        await db.execute(
            select(func.coalesce(func.sum(CodeAllocation.quantity), 0)).where(
                CodeAllocation.tenant_id == tenant_id,
                CodeAllocation.batch_id == batch_id,
            )
        )
    ).scalar()
    return int(total or 0)


async def _get_distributor(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    distributor_id: uuid.UUID,
    require_active: bool = True,
) -> Distributor | None:
    distributor = (
        await db.execute(
            select(Distributor).where(Distributor.id == distributor_id, Distributor.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not distributor:
        return None
    if require_active and distributor.status != "active":
        raise ValueError("请选择启用状态的经销商")
    return distributor


async def _get_region(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    region_id: uuid.UUID,
    require_active: bool = True,
) -> Region | None:
    region = (
        await db.execute(select(Region).where(Region.id == region_id, Region.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if not region:
        return None
    if require_active and region.status != "active":
        raise ValueError("请选择启用状态的区域")
    return region


async def get_channel_overview(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    distributor_count = (
        await db.execute(select(func.count()).select_from(Distributor).where(Distributor.tenant_id == tenant_id))
    ).scalar() or 0
    region_count = (
        await db.execute(select(func.count()).select_from(Region).where(Region.tenant_id == tenant_id))
    ).scalar() or 0
    store_count = (
        await db.execute(
            select(func.count()).select_from(Store).where(Store.tenant_id == tenant_id, Store.status == "active")
        )
    ).scalar() or 0
    allocated_quantity = (
        await db.execute(
            select(func.coalesce(func.sum(CodeAllocation.quantity), 0)).where(CodeAllocation.tenant_id == tenant_id)
        )
    ).scalar() or 0
    pending_diversion_count = (
        await db.execute(
            select(func.count())
            .select_from(DiversionClue)
            .where(
                DiversionClue.tenant_id == tenant_id,
                DiversionClue.resolved.is_(False),
            )
        )
    ).scalar() or 0
    return {
        "distributor_count": distributor_count,
        "region_count": region_count,
        "store_count": store_count,
        "allocated_quantity": int(allocated_quantity),
        "pending_diversion_count": pending_diversion_count,
    }


# ── 经销商 ────────────────────────────────────────


async def create_distributor(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    code: str | None = None,
    contact_name: str | None = None,
    contact_phone: str | None = None,
    status: str = "active",
) -> Distributor:
    phone_encrypted = None
    phone_hash = None
    if contact_phone:
        from app.utils.crypto import encrypt_phone, hash_phone

        phone_encrypted = encrypt_phone(contact_phone)
        phone_hash = hash_phone(contact_phone)

    dist = Distributor(
        tenant_id=tenant_id,
        name=name,
        code=code or await generate_channel_code(db, tenant_id, Distributor, "DIST"),
        contact_name=contact_name,
        contact_phone_encrypted=phone_encrypted,
        contact_phone_hash=phone_hash,
        status=status,
    )
    db.add(dist)
    await db.flush()
    await db.refresh(dist)
    return dist


async def list_distributors(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    status: str | None = None,
) -> tuple[list[Distributor], int]:
    conditions = [Distributor.tenant_id == tenant_id]
    if q:
        pattern = _like(q)
        conditions.append(or_(Distributor.name.ilike(pattern), Distributor.code.ilike(pattern)))
    if status:
        conditions.append(Distributor.status == status)

    total = (await db.execute(select(func.count()).select_from(Distributor).where(*conditions))).scalar() or 0
    rows = (
        (
            await db.execute(
                select(Distributor)
                .where(*conditions)
                .order_by(Distributor.updated_at.desc(), Distributor.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def get_distributor_stats(db: AsyncSession, tenant_id: uuid.UUID, distributor_ids: list[uuid.UUID]) -> dict:
    if not distributor_ids:
        return {}
    stats = {did: {"region_count": 0, "store_count": 0, "allocated_quantity": 0} for did in distributor_ids}

    region_rows = (
        await db.execute(
            select(Region.distributor_id, func.count().label("count"))
            .where(Region.tenant_id == tenant_id, Region.distributor_id.in_(distributor_ids))
            .group_by(Region.distributor_id)
        )
    ).all()
    for row in region_rows:
        stats[row.distributor_id]["region_count"] = row.count

    store_rows = (
        await db.execute(
            select(Store.distributor_id, func.count().label("count"))
            .where(Store.tenant_id == tenant_id, Store.distributor_id.in_(distributor_ids), Store.status == "active")
            .group_by(Store.distributor_id)
        )
    ).all()
    for row in store_rows:
        stats[row.distributor_id]["store_count"] = row.count

    alloc_rows = (
        await db.execute(
            select(CodeAllocation.distributor_id, func.coalesce(func.sum(CodeAllocation.quantity), 0).label("quantity"))
            .where(CodeAllocation.tenant_id == tenant_id, CodeAllocation.distributor_id.in_(distributor_ids))
            .group_by(CodeAllocation.distributor_id)
        )
    ).all()
    for row in alloc_rows:
        stats[row.distributor_id]["allocated_quantity"] = int(row.quantity or 0)
    return stats


async def update_distributor(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    distributor_id: uuid.UUID,
    name: str | None = None,
    status: str | None = None,
) -> Distributor | None:
    result = await db.execute(
        select(Distributor).where(Distributor.id == distributor_id, Distributor.tenant_id == tenant_id)
    )
    dist = result.scalar_one_or_none()
    if not dist:
        return None
    if name is not None:
        dist.name = name
    if status is not None:
        dist.status = status
    await db.flush()
    await db.refresh(dist)
    return dist


# ── 区域 ──────────────────────────────────────────


async def create_region(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    code: str | None = None,
    province: str | None = None,
    city: str | None = None,
    coverage_type: str | None = None,
    coverage_areas: list[dict] | None = None,
    distributor_id: uuid.UUID | None = None,
    status: str = "active",
) -> Region:
    if not distributor_id:
        raise ValueError("区域必须绑定经销商")
    distributor = await _get_distributor(db, tenant_id, distributor_id)
    if not distributor:
        raise ValueError("经销商不存在")
    normalized_type, normalized_province, normalized_city, normalized_areas = normalize_region_coverage(
        coverage_type,
        province,
        city,
        coverage_areas,
    )
    region = Region(
        tenant_id=tenant_id,
        name=name,
        code=code or await generate_channel_code(db, tenant_id, Region, "REG"),
        province=normalized_province,
        city=normalized_city,
        coverage_type=normalized_type,
        coverage_areas=normalized_areas,
        distributor_id=distributor_id,
        status=status,
    )
    db.add(region)
    await db.flush()
    await db.refresh(region)
    return region


async def list_regions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    status: str | None = None,
    distributor_id: uuid.UUID | None = None,
) -> tuple[list[Region], int]:
    conditions = [Region.tenant_id == tenant_id]
    if q:
        pattern = _like(q)
        conditions.append(
            or_(
                Region.name.ilike(pattern),
                Region.code.ilike(pattern),
                Region.province.ilike(pattern),
                Region.city.ilike(pattern),
            )
        )
    if status:
        conditions.append(Region.status == status)
    if distributor_id:
        conditions.append(Region.distributor_id == distributor_id)

    total = (await db.execute(select(func.count()).select_from(Region).where(*conditions))).scalar() or 0
    rows = (
        (
            await db.execute(
                select(Region)
                .where(*conditions)
                .order_by(Region.updated_at.desc(), Region.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def get_region_stats(db: AsyncSession, tenant_id: uuid.UUID, regions: list[Region]) -> dict:
    if not regions:
        return {}
    region_ids = [r.id for r in regions]
    distributor_ids = [r.distributor_id for r in regions if r.distributor_id]
    stats = {rid: {"store_count": 0, "distributor_name": None, "allocated_quantity": 0} for rid in region_ids}

    if distributor_ids:
        dist_rows = (
            (
                await db.execute(
                    select(Distributor).where(Distributor.tenant_id == tenant_id, Distributor.id.in_(distributor_ids))
                )
            )
            .scalars()
            .all()
        )
        names = {d.id: d.name for d in dist_rows}
        for region in regions:
            if region.distributor_id:
                stats[region.id]["distributor_name"] = names.get(region.distributor_id)

    store_rows = (
        await db.execute(
            select(Store.region_id, func.count().label("count"))
            .where(Store.tenant_id == tenant_id, Store.region_id.in_(region_ids), Store.status == "active")
            .group_by(Store.region_id)
        )
    ).all()
    for row in store_rows:
        stats[row.region_id]["store_count"] = row.count

    alloc_rows = (
        await db.execute(
            select(CodeAllocation.region_id, func.coalesce(func.sum(CodeAllocation.quantity), 0).label("quantity"))
            .where(CodeAllocation.tenant_id == tenant_id, CodeAllocation.region_id.in_(region_ids))
            .group_by(CodeAllocation.region_id)
        )
    ).all()
    for row in alloc_rows:
        stats[row.region_id]["allocated_quantity"] = int(row.quantity or 0)
    return stats


async def update_region(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    region_id: uuid.UUID,
    name: str | None = None,
    province: str | None = None,
    city: str | None = None,
    coverage_type: str | None = None,
    coverage_areas: list[dict] | None = None,
    distributor_id: uuid.UUID | None = None,
    status: str | None = None,
) -> Region | None:
    result = await db.execute(select(Region).where(Region.id == region_id, Region.tenant_id == tenant_id))
    region = result.scalar_one_or_none()
    if not region:
        return None
    if name is not None:
        region.name = name
    if coverage_type is not None or coverage_areas is not None or province is not None or city is not None:
        normalized_type, normalized_province, normalized_city, normalized_areas = normalize_region_coverage(
            coverage_type or region.coverage_type,
            province if province is not None else region.province,
            city if city is not None else region.city,
            coverage_areas if coverage_areas is not None else region.coverage_areas,
        )
        region.coverage_type = normalized_type
        region.province = normalized_province
        region.city = normalized_city
        region.coverage_areas = normalized_areas
    if distributor_id is not None:
        distributor = await _get_distributor(db, tenant_id, distributor_id)
        if not distributor:
            raise ValueError("经销商不存在")
        region.distributor_id = distributor_id
    if status is not None:
        region.status = status
    await db.flush()
    await db.refresh(region)
    return region


# ── 门店 ──────────────────────────────────────────


async def create_store(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    code: str | None = None,
    region_id: uuid.UUID | None = None,
    distributor_id: uuid.UUID | None = None,
    address: str | None = None,
) -> Store:
    if region_id:
        region = await _get_region(db, tenant_id, region_id)
        if not region:
            raise ValueError("区域不存在")
        distributor_id = distributor_id or region.distributor_id
    if distributor_id:
        distributor = await _get_distributor(db, tenant_id, distributor_id)
        if not distributor:
            raise ValueError("经销商不存在")
    store = Store(
        tenant_id=tenant_id,
        name=name,
        code=code or await generate_channel_code(db, tenant_id, Store, "STORE"),
        region_id=region_id,
        distributor_id=distributor_id,
        address=address,
    )
    db.add(store)
    await db.flush()
    await db.refresh(store)
    return store


async def list_stores(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    region_id: uuid.UUID | None = None,
    distributor_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    status: str | None = "active",
) -> tuple[list[Store], int]:
    conditions = [Store.tenant_id == tenant_id]
    if status:
        conditions.append(Store.status == status)
    if region_id:
        conditions.append(Store.region_id == region_id)
    if distributor_id:
        conditions.append(Store.distributor_id == distributor_id)
    if q:
        pattern = _like(q)
        conditions.append(or_(Store.name.ilike(pattern), Store.code.ilike(pattern), Store.address.ilike(pattern)))

    total = (await db.execute(select(func.count()).select_from(Store).where(*conditions))).scalar() or 0
    rows = (
        (
            await db.execute(
                select(Store)
                .where(*conditions)
                .order_by(Store.updated_at.desc(), Store.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def get_store_stats(db: AsyncSession, tenant_id: uuid.UUID, stores: list[Store]) -> dict:
    if not stores:
        return {}
    store_ids = [s.id for s in stores]
    region_ids = [s.region_id for s in stores if s.region_id]
    distributor_ids = [s.distributor_id for s in stores if s.distributor_id]
    stats = {sid: {"region_name": None, "distributor_name": None, "allocated_quantity": 0} for sid in store_ids}

    if region_ids:
        regions = (
            (await db.execute(select(Region).where(Region.tenant_id == tenant_id, Region.id.in_(region_ids))))
            .scalars()
            .all()
        )
        names = {r.id: r.name for r in regions}
        for store in stores:
            if store.region_id:
                stats[store.id]["region_name"] = names.get(store.region_id)

    if distributor_ids:
        dists = (
            (
                await db.execute(
                    select(Distributor).where(Distributor.tenant_id == tenant_id, Distributor.id.in_(distributor_ids))
                )
            )
            .scalars()
            .all()
        )
        names = {d.id: d.name for d in dists}
        for store in stores:
            if store.distributor_id:
                stats[store.id]["distributor_name"] = names.get(store.distributor_id)

    alloc_rows = (
        await db.execute(
            select(CodeAllocation.store_id, func.coalesce(func.sum(CodeAllocation.quantity), 0).label("quantity"))
            .where(CodeAllocation.tenant_id == tenant_id, CodeAllocation.store_id.in_(store_ids))
            .group_by(CodeAllocation.store_id)
        )
    ).all()
    for row in alloc_rows:
        stats[row.store_id]["allocated_quantity"] = int(row.quantity or 0)
    return stats


async def update_store(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    name: str | None = None,
    region_id: uuid.UUID | None = None,
    distributor_id: uuid.UUID | None = None,
    address: str | None = None,
    status: str | None = None,
) -> Store | None:
    result = await db.execute(select(Store).where(Store.id == store_id, Store.tenant_id == tenant_id))
    store = result.scalar_one_or_none()
    if not store:
        return None
    if name is not None:
        store.name = name
    if region_id is not None:
        region = await _get_region(db, tenant_id, region_id)
        if not region:
            raise ValueError("区域不存在")
        store.region_id = region_id
        if not distributor_id and region.distributor_id:
            store.distributor_id = region.distributor_id
    if distributor_id is not None:
        distributor = await _get_distributor(db, tenant_id, distributor_id)
        if not distributor:
            raise ValueError("经销商不存在")
        store.distributor_id = distributor_id
    if address is not None:
        store.address = address
    if status is not None:
        store.status = status
    await db.flush()
    await db.refresh(store)
    return store


async def delete_store(db: AsyncSession, tenant_id: uuid.UUID, store_id: uuid.UUID) -> bool:
    result = await db.execute(select(Store).where(Store.id == store_id, Store.tenant_id == tenant_id))
    store = result.scalar_one_or_none()
    if not store:
        return False
    store.status = "inactive"
    await db.flush()
    return True


# ── 渠道流向登记 ──────────────────────────────────────


async def assign_batch_to_channel(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    distributor_id: uuid.UUID | None = None,
    region_id: uuid.UUID | None = None,
) -> dict | None:
    """将码批次分配给经销商/区域（批次级）"""
    result = await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id))
    batch = result.scalar_one_or_none()
    if not batch:
        return None

    if distributor_id:
        batch.distributor_id = distributor_id
    if region_id:
        batch.region_id = region_id
    await db.flush()
    await db.refresh(batch)

    return {
        "id": str(batch.id),
        "distributor_id": str(batch.distributor_id) if batch.distributor_id else None,
        "region_id": str(batch.region_id) if batch.region_id else None,
    }


async def allocate_codes_to_store(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    store_id: uuid.UUID | None,
    quantity: int,
    distributor_id: uuid.UUID | None = None,
    region_id: uuid.UUID | None = None,
) -> CodeAllocation | None:
    """将码批次的一部分分配给经销商、区域或门店。"""
    batch = (
        await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if not batch:
        return None

    store = None
    region = None
    distributor = None

    if store_id:
        store = (
            await db.execute(select(Store).where(Store.id == store_id, Store.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if not store:
            return None
        region_id = store.region_id or region_id
        distributor_id = store.distributor_id or distributor_id

    if region_id:
        region = await _get_region(db, tenant_id, region_id)
        if not region:
            return None
        distributor_id = distributor_id or region.distributor_id
        batch.region_id = region.id

    if distributor_id:
        distributor = await _get_distributor(db, tenant_id, distributor_id)
        if not distributor:
            return None
        batch.distributor_id = distributor.id

    if not store_id and not region_id and not distributor_id:
        raise ValueError("请选择经销商、区域或门店作为分配目标")

    allocated = await _sum_allocated(db, tenant_id, batch_id)
    remaining = batch.quantity - allocated
    if quantity > remaining:
        raise ValueError(f"登记数量超过当前剩余码量，剩余 {remaining} 个")

    alloc = CodeAllocation(
        tenant_id=tenant_id,
        batch_id=batch_id,
        store_id=store_id,
        region_id=region.id if region else region_id,
        distributor_id=distributor.id if distributor else distributor_id,
        quantity=quantity,
        allocated_at=utcnow().replace(microsecond=0).isoformat(),
    )
    db.add(alloc)
    await db.flush()
    await db.refresh(alloc)
    return alloc


async def list_allocations(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID | None = None,
    store_id: uuid.UUID | None = None,
    region_id: uuid.UUID | None = None,
    distributor_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CodeAllocation], int]:
    """查询渠道流向登记记录"""
    conditions = [CodeAllocation.tenant_id == tenant_id]
    if batch_id:
        conditions.append(CodeAllocation.batch_id == batch_id)
    if store_id:
        conditions.append(CodeAllocation.store_id == store_id)
    if region_id:
        conditions.append(CodeAllocation.region_id == region_id)
    if distributor_id:
        conditions.append(CodeAllocation.distributor_id == distributor_id)

    total = (await db.execute(select(func.count()).select_from(CodeAllocation).where(*conditions))).scalar() or 0
    rows = (
        (
            await db.execute(
                select(CodeAllocation)
                .where(*conditions)
                .order_by(CodeAllocation.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def allocation_to_dict(db: AsyncSession, tenant_id: uuid.UUID, alloc: CodeAllocation) -> dict:
    batch = (
        await db.execute(select(CodeBatch).where(CodeBatch.id == alloc.batch_id, CodeBatch.tenant_id == tenant_id))
    ).scalar_one_or_none()
    store = None
    distributor = None
    region = None
    product = None
    sku = None

    if alloc.store_id:
        store = (
            await db.execute(select(Store).where(Store.id == alloc.store_id, Store.tenant_id == tenant_id))
        ).scalar_one_or_none()
    if alloc.distributor_id:
        distributor = (
            await db.execute(
                select(Distributor).where(Distributor.id == alloc.distributor_id, Distributor.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
    if alloc.region_id:
        region = (
            await db.execute(select(Region).where(Region.id == alloc.region_id, Region.tenant_id == tenant_id))
        ).scalar_one_or_none()
    elif store and store.region_id:
        region = (
            await db.execute(select(Region).where(Region.id == store.region_id, Region.tenant_id == tenant_id))
        ).scalar_one_or_none()
    elif batch and batch.region_id:
        region = (
            await db.execute(select(Region).where(Region.id == batch.region_id, Region.tenant_id == tenant_id))
        ).scalar_one_or_none()
    if batch:
        product = (
            await db.execute(select(Product).where(Product.id == batch.product_id, Product.tenant_id == tenant_id))
        ).scalar_one_or_none()
        sku = (
            await db.execute(select(SKU).where(SKU.id == batch.sku_id, SKU.tenant_id == tenant_id))
        ).scalar_one_or_none()
    allocated = await _sum_allocated(db, tenant_id, alloc.batch_id)
    batch_quantity = batch.quantity if batch else 0
    return {
        "id": str(alloc.id),
        "batch_id": str(alloc.batch_id),
        "batch_code": batch.batch_code if batch else None,
        "batch_quantity": batch_quantity,
        "allocated_quantity": allocated,
        "remaining_quantity": max(batch_quantity - allocated, 0),
        "store_id": str(alloc.store_id) if alloc.store_id else None,
        "store_name": store.name if store else None,
        "distributor_id": str(alloc.distributor_id) if alloc.distributor_id else None,
        "distributor_name": distributor.name if distributor else None,
        "region_id": str(region.id) if region else None,
        "region_name": region.name if region else None,
        "product_name": product.name if product else None,
        "sku_name": sku.name if sku else None,
        "quantity": alloc.quantity,
        "allocated_at": alloc.allocated_at,
    }


async def resolve_store_for_code(
    db: AsyncSession,
    public_id: str,
) -> dict | None:
    """扫码时自动匹配门店归属"""
    item = (await db.execute(select(CodeItem).where(CodeItem.public_id == public_id))).scalar_one_or_none()
    if not item:
        return None

    alloc = (
        await db.execute(
            select(CodeAllocation)
            .where(CodeAllocation.batch_id == item.code_batch_id)
            .order_by(CodeAllocation.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not alloc or not alloc.store_id:
        return None

    store = (await db.execute(select(Store).where(Store.id == alloc.store_id))).scalar_one_or_none()
    if not store:
        return None

    result = {
        "store_id": str(store.id),
        "store_name": store.name,
        "store_code": store.code,
        "address": store.address,
    }

    if store.region_id:
        region = (await db.execute(select(Region).where(Region.id == store.region_id))).scalar_one_or_none()
        if region:
            result["region_name"] = region.name
            result["city"] = region.city

    if store.distributor_id:
        dist = (
            await db.execute(select(Distributor).where(Distributor.id == store.distributor_id))
        ).scalar_one_or_none()
        if dist:
            result["distributor_name"] = dist.name

    return result


# ── 账号范围和渠道入口 ─────────────────────────────


async def create_account_scope(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    scope_type: str,
    distributor_id: uuid.UUID | None = None,
    region_id: uuid.UUID | None = None,
    store_id: uuid.UUID | None = None,
) -> AccountChannelScope:
    if scope_type == "distributor" and not distributor_id:
        raise ValueError("经销商账号必须绑定经销商")
    if scope_type == "region" and not region_id:
        raise ValueError("区域账号必须绑定区域")
    if scope_type == "store" and not store_id:
        raise ValueError("门店账号必须绑定门店")

    if distributor_id and not await _get_distributor(db, tenant_id, distributor_id):
        raise ValueError("经销商不存在")
    if region_id:
        region = await _get_region(db, tenant_id, region_id)
        if not region:
            raise ValueError("区域不存在")
        distributor_id = distributor_id or region.distributor_id
    if store_id:
        store = (
            await db.execute(select(Store).where(Store.id == store_id, Store.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if not store:
            raise ValueError("门店不存在")
        distributor_id = distributor_id or store.distributor_id
        region_id = region_id or store.region_id

    existing = (
        await db.execute(
            select(AccountChannelScope).where(
                AccountChannelScope.tenant_id == tenant_id,
                AccountChannelScope.account_id == account_id,
                AccountChannelScope.scope_type == scope_type,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.distributor_id = distributor_id
        existing.region_id = region_id
        existing.store_id = store_id
        await db.flush()
        await db.refresh(existing)
        return existing

    scope = AccountChannelScope(
        tenant_id=tenant_id,
        account_id=account_id,
        scope_type=scope_type,
        distributor_id=distributor_id,
        region_id=region_id,
        store_id=store_id,
    )
    db.add(scope)
    await db.flush()
    await db.refresh(scope)
    return scope


async def list_account_scopes(db: AsyncSession, tenant_id: uuid.UUID) -> list[AccountChannelScope]:
    rows = (
        (
            await db.execute(
                select(AccountChannelScope)
                .where(AccountChannelScope.tenant_id == tenant_id)
                .order_by(AccountChannelScope.updated_at.desc(), AccountChannelScope.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def delete_account_scope(db: AsyncSession, tenant_id: uuid.UUID, scope_id: uuid.UUID) -> bool:
    scope = (
        await db.execute(
            select(AccountChannelScope).where(
                AccountChannelScope.id == scope_id,
                AccountChannelScope.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not scope:
        return False
    await db.delete(scope)
    await db.flush()
    return True


async def get_account_scope(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    scope_type: str,
) -> AccountChannelScope | None:
    return (
        await db.execute(
            select(AccountChannelScope).where(
                AccountChannelScope.tenant_id == tenant_id,
                AccountChannelScope.account_id == account_id,
                AccountChannelScope.scope_type == scope_type,
            )
        )
    ).scalar_one_or_none()


async def get_distributor_portal_summary(db: AsyncSession, tenant_id: uuid.UUID, account_id: uuid.UUID) -> dict | None:
    scope = await get_account_scope(db, tenant_id, account_id, "distributor")
    region_scope = None
    if not scope:
        region_scope = await get_account_scope(db, tenant_id, account_id, "region")
        scope = region_scope
    if region_scope:
        if not region_scope.region_id:
            return None
        region = await _get_region(db, tenant_id, region_scope.region_id, require_active=False)
        if not region:
            return None
        stats = (await get_region_stats(db, tenant_id, [region])).get(region.id, {})
        pending_clues = (
            await db.execute(
                select(func.count())
                .select_from(DiversionClue)
                .where(
                    DiversionClue.tenant_id == tenant_id,
                    DiversionClue.region_id == region.id,
                    DiversionClue.resolved.is_(False),
                )
            )
        ).scalar() or 0
        allocations, total_allocations = await list_allocations(
            db,
            tenant_id,
            region_id=region.id,
            page=1,
            page_size=5,
        )
        return {
            "scope": {"type": "region", "id": str(region.id), "name": region.name},
            "regions": [
                {
                    "id": str(region.id),
                    "name": region.name,
                    "city": region.city,
                    "allocated_quantity": stats.get("allocated_quantity", 0),
                    "store_count": stats.get("store_count", 0),
                }
            ],
            "region_count": 1,
            "store_count": stats.get("store_count", 0),
            "allocated_quantity": stats.get("allocated_quantity", 0),
            "pending_diversion_count": pending_clues,
            "allocation_count": total_allocations,
            "recent_allocations": [await allocation_to_dict(db, tenant_id, alloc) for alloc in allocations],
        }

    if not scope or not scope.distributor_id:
        return None
    distributor = (
        await db.execute(
            select(Distributor).where(Distributor.id == scope.distributor_id, Distributor.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not distributor:
        return None
    stats = (await get_distributor_stats(db, tenant_id, [distributor.id])).get(distributor.id, {})
    pending_clues = (
        await db.execute(
            select(func.count())
            .select_from(DiversionClue)
            .where(
                DiversionClue.tenant_id == tenant_id,
                DiversionClue.distributor_id == distributor.id,
                DiversionClue.resolved.is_(False),
            )
        )
    ).scalar() or 0
    allocations, total_allocations = await list_allocations(
        db,
        tenant_id,
        distributor_id=distributor.id,
        page=1,
        page_size=5,
    )
    regions, _ = await list_regions(db, tenant_id, distributor_id=distributor.id, page=1, page_size=100)
    region_stats = await get_region_stats(db, tenant_id, regions)
    return {
        "scope": {"type": "distributor", "id": str(distributor.id), "name": distributor.name},
        "regions": [
            {
                "id": str(region.id),
                "name": region.name,
                "city": region.city,
                "allocated_quantity": region_stats.get(region.id, {}).get("allocated_quantity", 0),
                "store_count": region_stats.get(region.id, {}).get("store_count", 0),
            }
            for region in regions
        ],
        "region_count": stats.get("region_count", 0),
        "store_count": stats.get("store_count", 0),
        "allocated_quantity": stats.get("allocated_quantity", 0),
        "pending_diversion_count": pending_clues,
        "allocation_count": total_allocations,
        "recent_allocations": [await allocation_to_dict(db, tenant_id, alloc) for alloc in allocations],
    }


async def get_store_portal_summary(db: AsyncSession, tenant_id: uuid.UUID, account_id: uuid.UUID) -> dict | None:
    scope = await get_account_scope(db, tenant_id, account_id, "store")
    if not scope or not scope.store_id:
        return None
    store = (
        await db.execute(select(Store).where(Store.id == scope.store_id, Store.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if not store:
        return None
    stats = (await get_store_stats(db, tenant_id, [store])).get(store.id, {})
    allocations, total_allocations = await list_allocations(db, tenant_id, store_id=store.id, page=1, page_size=5)
    return {
        "scope": {"type": "store", "id": str(store.id), "name": store.name, "code": store.code},
        "address": store.address,
        "status": store.status,
        "region_name": stats.get("region_name"),
        "distributor_name": stats.get("distributor_name"),
        "allocated_quantity": stats.get("allocated_quantity", 0),
        "allocation_count": total_allocations,
        "recent_allocations": [await allocation_to_dict(db, tenant_id, alloc) for alloc in allocations],
    }


# ── 窜货检测 ──────────────────────────────────────


def _resolve_ip(ip: str) -> str | None:
    from app.services.geoip import resolve_ip_to_city

    return resolve_ip_to_city(ip)


async def get_code_expected_region(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
) -> dict | None:
    """获取码的归属区域信息（通过 CodeAllocation → Store → Region 链路，回退到 CodeBatch.region_id）"""
    item = (
        await db.execute(
            select(CodeItem).where(CodeItem.public_id == public_id, CodeItem.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not item or not item.code_batch_id:
        return None

    alloc = (
        await db.execute(
            select(CodeAllocation)
            .where(
                CodeAllocation.batch_id == item.code_batch_id,
                CodeAllocation.tenant_id == tenant_id,
            )
            .order_by(CodeAllocation.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if alloc and alloc.store_id:
        store = (
            await db.execute(
                select(Store).where(Store.id == alloc.store_id, Store.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if store and store.region_id:
            region = (
                await db.execute(
                    select(Region).where(Region.id == store.region_id, Region.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if region:
                return {
                    "city": region.city,
                    "coverage_type": region.coverage_type,
                    "coverage_areas": region.coverage_areas,
                    "coverage_label": region_coverage_label(region),
                    "expected_region": _expected_region_label(region),
                    "region_matches_detected_city": lambda detected_city: _region_matches_detected_city(
                        region, detected_city
                    ),
                    "region_id": str(region.id),
                    "region_name": region.name,
                    "store_id": str(store.id),
                    "store_name": store.name,
                    "distributor_id": str(store.distributor_id) if store.distributor_id else None,
                }

    batch = (
        await db.execute(
            select(CodeBatch).where(CodeBatch.id == item.code_batch_id, CodeBatch.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if batch and batch.region_id:
        region = (
            await db.execute(
                select(Region).where(Region.id == batch.region_id, Region.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if region:
            return {
                "city": region.city,
                "coverage_type": region.coverage_type,
                "coverage_areas": region.coverage_areas,
                "coverage_label": region_coverage_label(region),
                "expected_region": _expected_region_label(region),
                "region_matches_detected_city": lambda detected_city: _region_matches_detected_city(
                    region, detected_city
                ),
                "region_id": str(region.id),
                "region_name": region.name,
                "distributor_id": str(batch.distributor_id) if batch.distributor_id else None,
            }

    return None


async def check_diversion(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
    ip: str,
) -> DiversionClue | None:
    """检测窜货：扫码 IP 城市与码归属区域不匹配（支持门店级和批次级两种链路）"""
    # 幂等性检查：该码是否已有未处理的窜货线索
    existing_result = await db.execute(
        select(DiversionClue).where(
            DiversionClue.tenant_id == tenant_id,
            DiversionClue.public_id == public_id,
            DiversionClue.resolved.is_(False),
        )
    )
    if existing_result.scalar_one_or_none():
        return None  # 已有未处理线索，不重复创建

    detected_city = _resolve_ip(ip)
    if not detected_city:
        return None

    expected = await get_code_expected_region(db, tenant_id, public_id)
    if not expected:
        return None

    matcher = expected.get("region_matches_detected_city")
    if callable(matcher) and matcher(detected_city):
        return None
    if not callable(matcher) and detected_city == expected.get("city"):
        return None

    expected_region = expected.get("expected_region") or expected.get("city") or expected.get("region_name")
    if not expected_region:
        return None

    item = (await db.execute(select(CodeItem).where(CodeItem.public_id == public_id))).scalar_one_or_none()

    dist_id = None
    region_id = None
    if expected.get("distributor_id"):
        try:
            dist_id = uuid.UUID(expected["distributor_id"])
        except (ValueError, TypeError):
            pass
    if expected.get("region_id"):
        try:
            region_id = uuid.UUID(expected["region_id"])
        except (ValueError, TypeError):
            pass

    clue = DiversionClue(
        tenant_id=tenant_id,
        public_id=public_id,
        code_item_id=item.id if item else None,
        expected_region=expected_region,
        detected_city=detected_city,
        distributor_id=dist_id,
        region_id=region_id,
    )
    db.add(clue)
    await db.flush()
    await db.refresh(clue)

    # 自动创建风险通知
    from app.models.risk import RiskNotification

    notification = RiskNotification(
        tenant_id=tenant_id,
        notification_type="diversion_alert",
        title=f"疑似窜货：码 {clue.public_id}",
        detail=f"预期区域：{clue.expected_region or '未知'}，实际扫码城市：{clue.detected_city or '未知'}",
        code_item_id=clue.code_item_id,
        read=False,
    )
    db.add(notification)

    return clue


async def list_diversion_clues(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    resolved: bool | None = None,
    severity: str | None = None,
    page: int = 1,
    page_size: int = 20,
    distributor_id: uuid.UUID | None = None,
    region_id: uuid.UUID | None = None,
    q: str | None = None,
) -> tuple[list[DiversionClue], int]:
    conditions = [DiversionClue.tenant_id == tenant_id]
    if resolved is not None:
        conditions.append(DiversionClue.resolved == resolved)
    if distributor_id:
        conditions.append(DiversionClue.distributor_id == distributor_id)
    if region_id:
        conditions.append(DiversionClue.region_id == region_id)
    if q:
        pattern = _like(q)
        conditions.append(or_(DiversionClue.public_id.ilike(pattern), DiversionClue.detected_city.ilike(pattern)))

    total = (await db.execute(select(func.count()).select_from(DiversionClue).where(*conditions))).scalar() or 0
    rows = (
        (
            await db.execute(
                select(DiversionClue)
                .where(*conditions)
                .order_by(DiversionClue.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    if severity:
        filtered = [row for row in rows if _diversion_severity(row) == severity]
        return filtered, len(filtered)
    return list(rows), total


async def diversion_clue_to_dict(db: AsyncSession, tenant_id: uuid.UUID, clue: DiversionClue) -> dict:
    item = (
        await db.execute(select(CodeItem).where(CodeItem.id == clue.code_item_id, CodeItem.tenant_id == tenant_id))
    ).scalar_one_or_none()
    batch = None
    product = None
    sku = None
    if item:
        batch = (
            await db.execute(
                select(CodeBatch).where(CodeBatch.id == item.code_batch_id, CodeBatch.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
    if batch:
        product = (
            await db.execute(select(Product).where(Product.id == batch.product_id, Product.tenant_id == tenant_id))
        ).scalar_one_or_none()
        sku = (
            await db.execute(select(SKU).where(SKU.id == batch.sku_id, SKU.tenant_id == tenant_id))
        ).scalar_one_or_none()

    distributor = None
    region = None
    store = None
    allocation = None
    if clue.distributor_id:
        distributor = (
            await db.execute(
                select(Distributor).where(Distributor.id == clue.distributor_id, Distributor.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
    if clue.region_id:
        region = (
            await db.execute(select(Region).where(Region.id == clue.region_id, Region.tenant_id == tenant_id))
        ).scalar_one_or_none()
    if not distributor and region and region.distributor_id:
        distributor = (
            await db.execute(
                select(Distributor).where(Distributor.id == region.distributor_id, Distributor.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
    if batch:
        allocation = (
            await db.execute(
                select(CodeAllocation)
                .where(CodeAllocation.tenant_id == tenant_id, CodeAllocation.batch_id == batch.id)
                .order_by(CodeAllocation.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if allocation and allocation.store_id:
        store = (
            await db.execute(select(Store).where(Store.id == allocation.store_id, Store.tenant_id == tenant_id))
        ).scalar_one_or_none()

    latest_scan_time = (
        await db.execute(
            select(ScanEvent.scan_time)
            .where(ScanEvent.tenant_id == tenant_id, ScanEvent.public_id == clue.public_id)
            .order_by(ScanEvent.scan_time.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    return {
        "id": str(clue.id),
        "public_id": clue.public_id,
        "expected_region": clue.expected_region,
        "detected_city": clue.detected_city,
        "severity": _diversion_severity(clue),
        "batch_code": batch.batch_code if batch else None,
        "product_name": product.name if product else None,
        "sku_name": sku.name if sku else None,
        "distributor_id": str(clue.distributor_id) if clue.distributor_id else None,
        "distributor_name": distributor.name if distributor else None,
        "region_id": str(clue.region_id) if clue.region_id else None,
        "region_name": region.name if region else None,
        "store_name": store.name if store else None,
        "detected_at": _dt(latest_scan_time),
        "resolved": clue.resolved,
        "resolution_action": clue.resolution_action,
        "resolution_note": clue.resolution_note,
        "resolved_by_account_id": str(clue.resolved_by_account_id) if clue.resolved_by_account_id else None,
        "resolved_at": _dt(clue.resolved_at),
        "handling_recommendation": "联系渠道核实货物流向，确认是否窜货、临时调货或误报。",
    }
