"""一码通演示数据生成器 — 生成中等规模真实感演示数据"""

import asyncio
import hashlib
import random
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import typer
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 确保可以 import app 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.models.campaign import Benefit, BenefitClaim, Campaign, CampaignStatus
from app.models.channel import (
    AccountChannelScope,
    CodeAllocation,
    Distributor,
    DiversionClue,
    Region,
    Store,
)
from app.models.code import CodeBatch, CodeItem, CodeItemStatus
from app.models.connector import Connector  # noqa: F401 - register FK for Benefit.connector_id
from app.models.member import (
    ConsumerProfile,
    PointProduct,
    PointRedemption,
    PointRule,
    PointTransaction,
    PointTransactionType,
)
from app.models.page import PageTemplate, PageTemplateStatus, PageVersion, PageVersionStatus
from app.models.product import SKU, Brand, Product, ProductionBatch
from app.models.risk import InterceptionRecord, RiskAlert, RiskAlertType, RiskNotification, RiskRule
from app.models.scan import ScanEvent
from app.models.tenant import (
    Account,
    AgencyAuthorization,
    AgencyAuthStatus,
    Organization,
    Role,
    Tenant,
    account_roles,
)
from app.services.analytics import aggregate_daily_stats
from app.services.channel import create_account_scope
from app.services.code import activate_batch, create_code_batch
from app.services.product import create_brand, create_product, create_sku
from app.services.tenant import create_tenant
from app.utils import utcnow
from app.utils.security import hash_password

app = typer.Typer(help="一码通演示数据生成器")

engine = create_async_engine(str(settings.database_url))
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

TENANT_SLUG = "demo"
TENANT_NAME = "青岭良仓演示租户"
TOTAL_DAYS = 60
DEMO_ENABLED_FEATURES = {"channel_store": True}


# ─── 进度报告器 ──────────────────────────────────────────
class Progress:
    def __init__(self, total: int):
        self.total = total
        self.current = 0
        self.start = time.time()

    def step(self, label: str, detail: str = ""):
        self.current += 1
        elapsed = time.time() - self.start
        typer.echo(f"  {self.current}/{self.total} {label:<30s} ✓ {detail}  ({elapsed:.1f}s)")


# ─── 演示账号 ──────────────────────────────────────────
DEMO_ACCOUNTS = [
    {"email": "admin@demo.com", "password": "Admin1234", "name": "品牌管理员", "role": "admin", "title": "品牌管理员"},
    {"email": "ops@demo.com", "password": "Ops123456", "name": "活动运营", "role": "operator", "title": "活动运营"},
    {
        "email": "agency@demo.com",
        "password": "Agency1234",
        "name": "代运营顾问",
        "role": "operator",
        "title": "代运营顾问",
    },
    {
        "email": "dist@demo.com",
        "password": "Dist123456",
        "name": "华东经销商",
        "role": "distributor",
        "title": "华东经销商",
    },
    {
        "email": "store@demo.com",
        "password": "Store123456",
        "name": "南京东路店",
        "role": "store_guide",
        "title": "南京东路店",
    },
]

# ─── 品牌/产品/SKU 定义 ─────────────────────────────────
BRANDS_DATA = [
    {
        "name": "青岭良仓",
        "description": "主打产地可信、扫码领券和复购私域承接的农产品品牌。",
        "products": [
            {
                "name": "五常稻花香大米 5kg",
                "category": "大米",
                "origin": "黑龙江五常",
                "description": "五常核心产区稻花香大米，溯源、检测报告、首扫福利。",
                "skus": [
                    {
                        "code": "RICE-5KG-001",
                        "name": "五常稻花香 5kg 礼盒装",
                        "specs": {"净含量": "5kg", "产地": "黑龙江五常", "包装": "礼盒装"},
                    },
                    {
                        "code": "RICE-10KG-001",
                        "name": "五常稻花香 10kg 袋装",
                        "specs": {"净含量": "10kg", "产地": "黑龙江五常", "包装": "编织袋"},
                    },
                ],
            },
            {
                "name": "有机杂粮礼盒",
                "category": "杂粮",
                "origin": "内蒙古赤峰",
                "description": "6种有机杂粮组合礼盒，适合节日送礼。",
                "skus": [
                    {
                        "code": "GRAIN-GIFT-001",
                        "name": "有机杂粮礼盒 六合一",
                        "specs": {"净含量": "3kg", "产地": "内蒙古赤峰", "包装": "礼盒"},
                    },
                ],
            },
            {
                "name": "冷榨花生油 1.5L",
                "category": "食用油",
                "origin": "山东临沂",
                "description": "物理冷压榨花生油，保留原始香味。",
                "skus": [
                    {
                        "code": "OIL-15L-001",
                        "name": "冷榨花生油 1.5L 瓶装",
                        "specs": {"净含量": "1.5L", "产地": "山东临沂", "包装": "玻璃瓶"},
                    },
                    {
                        "code": "OIL-5L-001",
                        "name": "冷榨花生油 5L 桶装",
                        "specs": {"净含量": "5L", "产地": "山东临沂", "包装": "铁桶"},
                    },
                ],
            },
            {
                "name": "长白山椴树蜂蜜 500g",
                "category": "蜂蜜",
                "origin": "吉林长白山",
                "description": "长白山原始椴树蜜，扫码领试用装。",
                "skus": [
                    {
                        "code": "HONEY-500G-001",
                        "name": "椴树蜂蜜 500g 瓶装",
                        "specs": {"净含量": "500g", "产地": "吉林长白山", "包装": "玻璃瓶"},
                    },
                ],
            },
            {
                "name": "有机蔬菜周卡",
                "category": "蔬菜",
                "origin": "云南昆明",
                "description": "有机蔬菜周配送卡，扫码溯源农场信息。",
                "skus": [
                    {
                        "code": "VEG-WEEK-001",
                        "name": "有机蔬菜周卡 标准版",
                        "specs": {"配送次数": "7次", "产地": "云南昆明", "包装": "冷链配送"},
                    },
                ],
            },
        ],
    },
    {
        "name": "茶语清风",
        "description": "高端中国茶叶品牌，注重茶园溯源与品鉴体验。",
        "products": [
            {
                "name": "西湖龙井明前茶 100g",
                "category": "绿茶",
                "origin": "浙江杭州",
                "description": "明前一级龙井，扫码查看茶园视频。",
                "skus": [
                    {
                        "code": "TEA-LJ-100G",
                        "name": "龙井明前 100g 罐装",
                        "specs": {"净含量": "100g", "产地": "浙江杭州", "包装": "铁罐"},
                    },
                    {
                        "code": "TEA-LJ-250G",
                        "name": "龙井明前 250g 礼盒",
                        "specs": {"净含量": "250g", "产地": "浙江杭州", "包装": "礼盒"},
                    },
                ],
            },
            {
                "name": "正山小种红茶 200g",
                "category": "红茶",
                "origin": "福建武夷山",
                "description": "桐木关正山小种，传统松烟香。",
                "skus": [
                    {
                        "code": "TEA-XZ-200G",
                        "name": "正山小种 200g 罐装",
                        "specs": {"净含量": "200g", "产地": "福建武夷山", "包装": "铁罐"},
                    },
                ],
            },
            {
                "name": "白毫银针 50g",
                "category": "白茶",
                "origin": "福建福鼎",
                "description": "福鼎白毫银针，扫码查看采摘日期。",
                "skus": [
                    {
                        "code": "TEA-YZ-50G",
                        "name": "白毫银针 50g 罐装",
                        "specs": {"净含量": "50g", "产地": "福建福鼎", "包装": "铁罐"},
                    },
                ],
            },
        ],
    },
    {
        "name": "醉美庄园",
        "description": "精品国产葡萄酒品牌，产地直供。",
        "products": [
            {
                "name": "赤霞珠干红 750ml",
                "category": "葡萄酒",
                "origin": "宁夏贺兰山",
                "description": "贺兰山东麓赤霞珠干红，扫码品鉴笔记。",
                "skus": [
                    {
                        "code": "WINE-RED-750",
                        "name": "赤霞珠干红 750ml 单瓶",
                        "specs": {"净含量": "750ml", "产地": "宁夏贺兰山", "包装": "单瓶"},
                    },
                    {
                        "code": "WINE-RED-6PK",
                        "name": "赤霞珠干红 6瓶装",
                        "specs": {"净含量": "750ml×6", "产地": "宁夏贺兰山", "包装": "木箱"},
                    },
                ],
            },
            {
                "name": "冰酒 375ml",
                "category": "冰酒",
                "origin": "辽宁桓仁",
                "description": "桓仁冰酒，扫码查看酿造过程。",
                "skus": [
                    {
                        "code": "WINE-ICE-375",
                        "name": "冰酒 375ml 单瓶",
                        "specs": {"净含量": "375ml", "产地": "辽宁桓仁", "包装": "单瓶"},
                    },
                ],
            },
        ],
    },
]

# ─── 渠道定义 ──────────────────────────────────────────
DISTRIBUTORS_DATA = [
    {"code": "DEMO-DIST-EAST", "name": "华东经销商", "contact": "陈经理"},
    {"code": "DEMO-DIST-SOUTH", "name": "华南经销商", "contact": "李经理"},
    {"code": "DEMO-DIST-NORTH", "name": "华北经销商", "contact": "王经理"},
    {"code": "DEMO-DIST-SWEST", "name": "西南经销商", "contact": "赵经理"},
]

REGIONS_DATA = [
    {"code": "DEMO-REG-SH", "name": "上海区域", "province": "上海", "city": "上海", "dist": 0},
    {"code": "DEMO-REG-HZ", "name": "杭州区域", "province": "浙江", "city": "杭州", "dist": 0},
    {"code": "DEMO-REG-NJ", "name": "南京区域", "province": "江苏", "city": "南京", "dist": 0},
    {"code": "DEMO-REG-SZ", "name": "苏州区域", "province": "江苏", "city": "苏州", "dist": 0},
    {"code": "DEMO-REG-GZ", "name": "广州区域", "province": "广东", "city": "广州", "dist": 1},
    {"code": "DEMO-REG-SZN", "name": "深圳区域", "province": "广东", "city": "深圳", "dist": 1},
    {"code": "DEMO-REG-BJ", "name": "北京区域", "province": "北京", "city": "北京", "dist": 2},
    {"code": "DEMO-REG-CD", "name": "成都区域", "province": "四川", "city": "成都", "dist": 3},
]

STORES_DATA = [
    {"code": "DEMO-STORE-NJDL", "name": "南京东路店", "region": 0, "address": "上海市黄浦区南京东路"},
    {"code": "DEMO-STORE-XHW", "name": "徐汇万体馆店", "region": 0, "address": "上海市徐汇区漕溪北路"},
    {"code": "DEMO-STORE-XHWH", "name": "西湖文化广场店", "region": 1, "address": "杭州市下城区中山北路"},
    {"code": "DEMO-STORE-BINJ", "name": "滨江宝龙店", "region": 1, "address": "杭州市滨江区滨盛路"},
    {"code": "DEMO-STORE-XNK", "name": "新街口店", "region": 2, "address": "南京市玄武区中山路"},
    {"code": "DEMO-STORE-GYJ", "name": "观前街店", "region": 3, "address": "苏州市姑苏区观前街"},
    {"code": "DEMO-STORE-THC", "name": "天河城店", "region": 4, "address": "广州市天河区天河路"},
    {"code": "DEMO-STORE-HQB", "name": "华强北店", "region": 5, "address": "深圳市福田区华强北路"},
    {"code": "DEMO-STORE-WFJ", "name": "王府井店", "region": 6, "address": "北京市东城区王府井大街"},
    {"code": "DEMO-STORE-SSL", "name": "三里屯店", "region": 6, "address": "北京市朝阳区三里屯路"},
    {"code": "DEMO-STORE-CHP", "name": "春熙路店", "region": 7, "address": "成都市锦江区春熙路"},
    {"code": "DEMO-STORE-TFC", "name": "天府广场店", "region": 7, "address": "成都市青羊区人民西路"},
    {"code": "DEMO-STORE-PD", "name": "浦东陆家嘴店", "region": 0, "address": "上海市浦东新区陆家嘴环路"},
    {"code": "DEMO-STORE-YH", "name": "余杭万达店", "region": 1, "address": "杭州市余杭区文一西路"},
]

# ─── User-Agent 池 ──────────────────────────────────────
UA_WECHAT = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.47",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.44",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Version/4.0 Chrome/125.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.47",
    "Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Version/4.0 Chrome/122.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.45",
]
UA_ALIPAY = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 AlipayClient/10.5.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Version/4.0 Chrome/125.0.0.0 Mobile Safari/537.36 AlipayClient/10.5.36",
]
UA_BROWSER = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

# ─── 昵称池 ──────────────────────────────────────────
NICKNAME_POOL = [
    "小太阳",
    "大白兔",
    "小星星",
    "月光族",
    "吃货王",
    "奶茶控",
    "小确幸",
    "阳光少年",
    "棉花糖",
    "小米粒",
    "小蜜蜂",
    "快乐星球",
    "柠檬精",
    "薄荷糖",
    "小红帽",
    "彩虹糖",
    "小苹果",
    "樱桃小丸子",
    "草莓奶昔",
    "蓝莓芝士",
    "芒果冰沙",
    "椰子树",
    "西瓜太郎",
    "葡萄汽水",
    "蜜桃乌龙",
    "焦糖拿铁",
    "抹茶星冰乐",
    "可可布朗尼",
    "香草冰淇淋",
    "抹茶蛋糕",
    "巧克力慕斯",
    "芒果布丁",
    "草莓千层",
    "榴莲忘返",
    "菠萝油",
    "小龙虾",
    "大闸蟹",
    "狮子头",
    "佛跳墙",
    "宫保鸡丁",
    "麻婆豆腐",
    "红烧肉",
    "清蒸鲈鱼",
    "东坡肘子",
    "水煮牛肉",
    "回锅肉",
    "鱼香肉丝",
    "糖醋排骨",
    "蚂蚁上树",
    "夫妻肺片",
    "担担面",
    "热干面",
    "兰州拉面",
    "重庆小面",
    "螺蛳粉",
    "桂林米粉",
    "过桥米线",
    "云南米线",
    "武汉豆皮",
    "长沙臭豆腐",
    "煎饼果子",
    "肉夹馍",
    "羊肉泡馍",
    "凉皮",
    "冰粉",
    "豆花",
    "小熊猫",
    "柯基犬",
    "柴犬控",
    "猫薄荷",
    "布偶猫",
    "金毛",
    "哈士奇",
    "边牧",
    "秋田犬",
    "法斗",
    "英短",
    "美短",
    "橘猫",
    "暹罗猫",
    "加菲猫",
    "折耳猫",
    "北极熊",
    "企鹅",
    "海豚",
    "鲸鱼",
    "水母",
    "珊瑚",
    "海星",
    "小丑鱼",
    "绿叶",
    "红枫",
    "银杏",
    "梧桐",
    "松柏",
    "樱花",
    "桃花",
    "荷花",
    "向日葵",
    "薰衣草",
    "郁金香",
    "牡丹",
    "兰花",
    "菊花",
    "梅花",
    "竹叶青",
    "小确幸",
    "岁月静好",
    "且听风吟",
    "风和日丽",
    "春暖花开",
    "天高云淡",
    "星辰大海",
    "浮生若梦",
    "梦里花落",
    "云卷云舒",
    "半夏时光",
    "秋水长天",
    "冬日暖阳",
    "山间清风",
    "溪水潺潺",
    "竹林幽径",
    "月下独酌",
    "花间一壶酒",
    "大海无量",
    "春风十里",
    "桃花潭水",
    "月朦胧",
    "鸟朦胧",
    "江南烟雨",
    "塞北风沙",
    "大漠孤烟",
    "长河落日",
    "碧水蓝天",
    "青山绿水",
    "紫气东来",
    "小确幸",
    "满分先生",
    "元气少女",
    "阳光男孩",
    "甜甜圈",
    "棒棒糖",
    "风信子",
    "铃兰花",
    "满天星",
    "勿忘我",
    "紫罗兰",
    "百合花",
    "天狼星",
    "北极星",
    "织女星",
    "牛郎星",
    "猎户座",
    "仙女座",
    "小蜜蜂",
    "蝴蝶兰",
    "蒲公英",
    "牵牛花",
    "风铃草",
    "忘忧草",
    "棉花糖",
    "棒棒糖",
    "波板糖",
    "彩虹糖",
    "QQ糖",
    "牛轧糖",
    "小幸运",
    "好运来",
    "锦鲤",
    "旺财",
    "招财猫",
    "福星高照",
    "琴棋书画",
    "诗酒花茶",
    "笔墨纸砚",
    "梅兰竹菊",
    "春夏秋冬",
    "风花雪月",
    "翡翠",
    "琥珀",
    "玛瑙",
    "水晶",
    "珍珠",
    "琉璃",
    "珊瑚",
    "碧玉",
    "小火箭",
    "大飞船",
    "宇宙侠",
    "星空漫步",
    "银河系",
    "光年之外",
]

# ─── 环境权重分布 ────────────────────────────────────
ENV_WEIGHTS = {"wechat": 65, "browser": 20, "alipay": 10, "other": 5}
UA_MAP = {
    "wechat": UA_WECHAT,
    "alipay": UA_ALIPAY,
    "browser": UA_BROWSER,
    "other": UA_BROWSER,
}
ENV_OTHER_VALUES = ["qq", "douyin", "xiaohongshu"]


def _pick_env() -> tuple[str, str]:
    """按权重随机选择环境和 User-Agent"""
    r = random.randint(1, 100)
    if r <= 65:
        env = "wechat"
    elif r <= 85:
        env = "browser"
    elif r <= 95:
        env = "alipay"
    else:
        env = random.choice(ENV_OTHER_VALUES)
    ua = random.choice(UA_MAP.get(env, UA_BROWSER))
    return env, ua


def _scan_count_for_day(day_index: int) -> int:
    """计算第 day_index 天的扫码量（0=最早，TOTAL_DAYS-1=今天），模拟增长趋势"""
    base = 80
    growth = day_index * 7  # 每天增长 7 次
    weekday_factor = 1.3 if (date.today() - timedelta(days=TOTAL_DAYS - 1 - day_index)).weekday() < 5 else 0.85
    noise = random.randint(-20, 20)
    return max(20, int((base + growth + noise) * weekday_factor))


def _random_scan_time(day_index: int) -> datetime:
    """生成第 day_index 天的一个随机扫码时间（集中在高峰时段）"""
    target_date = date.today() - timedelta(days=TOTAL_DAYS - 1 - day_index)
    hour_weights = [
        (0, 6, 0.02),
        (6, 10, 0.10),
        (10, 12, 0.22),
        (12, 14, 0.12),
        (14, 16, 0.22),
        (16, 18, 0.14),
        (18, 20, 0.10),
        (20, 24, 0.08),
    ]
    r = random.random()
    cumulative = 0.0
    chosen_start = 0
    chosen_end = 24
    for start, end, weight in hour_weights:
        cumulative += weight
        if r <= cumulative:
            chosen_start = start
            chosen_end = end
            break
    hour = random.randint(chosen_start, chosen_end - 1)
    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    return datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        hour,
        minute,
        second,
    )


# ═══════════════════════════════════════════════════════
# 阶段 1: 租户与账号
# ═══════════════════════════════════════════════════════


async def _ensure_tenant(db: AsyncSession) -> Tenant:
    """获取或创建演示租户"""
    result = await db.execute(select(Tenant).where(Tenant.slug == TENANT_SLUG))
    tenant = result.scalar_one_or_none()
    if tenant:
        tenant.enabled_features = {**(tenant.enabled_features or {}), **DEMO_ENABLED_FEATURES}
        return tenant
    tenant = await create_tenant(
        db,
        name=TENANT_NAME,
        slug=TENANT_SLUG,
        plan="free",
        admin_email=DEMO_ACCOUNTS[0]["email"],
        admin_name=DEMO_ACCOUNTS[0]["name"],
        admin_password=DEMO_ACCOUNTS[0]["password"],
        tenant_type="brand",
    )
    tenant.enabled_features = {**(tenant.enabled_features or {}), **DEMO_ENABLED_FEATURES}
    return tenant


async def _ensure_org(db: AsyncSession, tenant_id: uuid.UUID) -> Organization:
    result = await db.execute(select(Organization).where(Organization.tenant_id == tenant_id).limit(1))
    org = result.scalar_one_or_none()
    if org:
        return org
    org = Organization(tenant_id=tenant_id, name="演示默认组织")
    db.add(org)
    await db.flush()
    await db.refresh(org)
    return org


async def _ensure_role(db: AsyncSession, tenant_id: uuid.UUID, name: str, description: str) -> Role:
    result = await db.execute(select(Role).where(Role.tenant_id == tenant_id, Role.name == name))
    role = result.scalar_one_or_none()
    if role:
        role.description = description
        return role
    role = Role(tenant_id=tenant_id, name=name, description=description)
    db.add(role)
    await db.flush()
    await db.refresh(role)
    return role


async def _ensure_accounts(db: AsyncSession, tenant_id: uuid.UUID, org_id: uuid.UUID) -> list[Account]:
    roles = {
        "admin": await _ensure_role(db, tenant_id, "admin", "品牌管理员：完整管理权限。"),
        "operator": await _ensure_role(db, tenant_id, "operator", "运营人员：日常运营权限。"),
        "distributor": await _ensure_role(db, tenant_id, "distributor", "经销商：查看本区域数据。"),
        "store_guide": await _ensure_role(db, tenant_id, "store_guide", "门店：查看本店数据。"),
    }
    accounts: list[Account] = []
    for item in DEMO_ACCOUNTS:
        result = await db.execute(select(Account).where(Account.tenant_id == tenant_id, Account.email == item["email"]))
        account = result.scalar_one_or_none()
        if account:
            account.name = item["name"]
            account.organization_id = org_id
            account.hashed_password = hash_password(item["password"])
        else:
            account = Account(
                tenant_id=tenant_id,
                organization_id=org_id,
                email=item["email"],
                hashed_password=hash_password(item["password"]),
                name=item["name"],
            )
            db.add(account)
            await db.flush()
        await db.execute(account_roles.delete().where(account_roles.c.account_id == account.id))
        await db.execute(account_roles.insert().values(account_id=account.id, role_id=roles[item["role"]].id))
        accounts.append(account)
    await db.flush()
    return accounts


# ═══════════════════════════════════════════════════════
# 阶段 2: 品牌与产品
# ═══════════════════════════════════════════════════════


async def _ensure_brands_products(db: AsyncSession, tenant_id: uuid.UUID) -> list[dict]:
    """创建所有品牌/产品/SKU/生产批次，返回结构化数据供后续引用"""
    brand_records = []
    for brand_data in BRANDS_DATA:
        # 品牌
        result = await db.execute(select(Brand).where(Brand.tenant_id == tenant_id, Brand.name == brand_data["name"]))
        brand = result.scalar_one_or_none()
        if not brand:
            brand = await create_brand(db, tenant_id, brand_data["name"], description=brand_data["description"])
        else:
            brand.description = brand_data["description"]
        await db.flush()

        product_records = []
        for prod_data in brand_data["products"]:
            result = await db.execute(
                select(Product).where(Product.tenant_id == tenant_id, Product.name == prod_data["name"])
            )
            product = result.scalar_one_or_none()
            if not product:
                product = await create_product(
                    db,
                    tenant_id,
                    brand.id,
                    prod_data["name"],
                    category=prod_data.get("category"),
                    origin=prod_data.get("origin"),
                    description=prod_data.get("description"),
                )
            else:
                product.category = prod_data.get("category")
                product.origin = prod_data.get("origin")
                product.description = prod_data.get("description")
            await db.flush()

            sku_records = []
            for sku_data in prod_data["skus"]:
                result = await db.execute(select(SKU).where(SKU.product_id == product.id, SKU.code == sku_data["code"]))
                sku = result.scalar_one_or_none()
                if not sku:
                    sku = await create_sku(
                        db,
                        tenant_id,
                        product.id,
                        sku_data["code"],
                        sku_data["name"],
                        specifications=sku_data.get("specs"),
                    )
                else:
                    sku.name = sku_data["name"]
                    sku.specifications = sku_data.get("specs")
                await db.flush()

                # 生产批次（每个 SKU 一个）
                batch_code = f"DEMO-{sku_data['code']}-2026"
                result = await db.execute(
                    select(ProductionBatch).where(
                        ProductionBatch.tenant_id == tenant_id,
                        ProductionBatch.batch_code == batch_code,
                    )
                )
                prod_batch = result.scalar_one_or_none()
                if not prod_batch:
                    prod_batch = ProductionBatch(
                        tenant_id=tenant_id,
                        product_id=product.id,
                        sku_id=sku.id,
                        batch_code=batch_code,
                        production_date=date.today() - timedelta(days=random.randint(15, 45)),
                        expiry_date=date.today() + timedelta(days=random.randint(270, 400)),
                        origin=prod_data.get("origin", ""),
                    )
                    db.add(prod_batch)
                    await db.flush()
                    await db.refresh(prod_batch)

                sku_records.append({"sku": sku, "production_batch": prod_batch})

            product_records.append({"product": product, "skus": sku_records})

        brand_records.append({"brand": brand, "products": product_records})

    await db.flush()
    return brand_records


# ═══════════════════════════════════════════════════════
# 阶段 3: 码批次与码项
# ═══════════════════════════════════════════════════════

# 每个品牌/产品分配的码数量
CODE_QUANTITIES = {
    "五常稻花香大米 5kg": 500,
    "有机杂粮礼盒": 200,
    "冷榨花生油 1.5L": 300,
    "长白山椴树蜂蜜 500g": 200,
    "有机蔬菜周卡": 150,
    "西湖龙井明前茶 100g": 200,
    "正山小种红茶 200g": 150,
    "白毫银针 50g": 100,
    "赤霞珠干红 750ml": 150,
    "冰酒 375ml": 80,
}


async def _ensure_code_batches(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    brand_records: list[dict],
) -> list[CodeItem]:
    """生成码批次并激活，返回所有激活的 CodeItem"""
    all_items: list[CodeItem] = []

    for brand_rec in brand_records:
        for prod_rec in brand_rec["products"]:
            product_name = prod_rec["product"].name
            quantity = CODE_QUANTITIES.get(product_name, 100)
            # 每个产品的第一个 SKU 生成码
            if not prod_rec["skus"]:
                continue
            sku_rec = prod_rec["skus"][0]
            sku = sku_rec["sku"]
            prod_batch = sku_rec["production_batch"]

            # 检查是否已存在该生产批次关联的码批次
            result = await db.execute(
                select(CodeBatch).where(
                    CodeBatch.tenant_id == tenant_id,
                    CodeBatch.production_batch_id == prod_batch.id,
                )
            )
            code_batch = result.scalar_one_or_none()

            if not code_batch:
                data = await create_code_batch(
                    db,
                    tenant_id,
                    prod_rec["product"].id,
                    sku.id,
                    prod_batch.id,
                    quantity,
                    created_by,
                )
                batch_id = uuid.UUID(data["id"])
                await activate_batch(db, tenant_id, batch_id)
                result = await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id))
                code_batch = result.scalar_one()

            # 获取该批次的码项
            result = await db.execute(
                select(CodeItem).where(
                    CodeItem.tenant_id == tenant_id,
                    CodeItem.code_batch_id == code_batch.id,
                )
            )
            items = list(result.scalars().all())
            all_items.extend(items)

    # 标记少量码为 revoked/frozen 状态（约 8%）
    activated_items = [i for i in all_items if i.status == CodeItemStatus.activated]
    revoke_count = max(1, len(activated_items) // 20)  # ~5%
    freeze_count = max(1, len(activated_items) // 33)  # ~3%
    now = utcnow()
    for item in activated_items[:revoke_count]:
        item.status = CodeItemStatus.revoked
        item.revoked_at = now
    for item in activated_items[revoke_count : revoke_count + freeze_count]:
        item.status = CodeItemStatus.frozen
    await db.flush()

    return all_items


# ═══════════════════════════════════════════════════════
# 阶段 4: 渠道体系
# ═══════════════════════════════════════════════════════


async def _ensure_channels(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    accounts: list[Account],
    code_items: list[CodeItem],
) -> dict:
    """创建经销商/区域/门店/码分配/账号渠道作用域，返回渠道数据"""
    # 经销商
    distributors = []
    for dist_data in DISTRIBUTORS_DATA:
        result = await db.execute(
            select(Distributor).where(Distributor.tenant_id == tenant_id, Distributor.code == dist_data["code"])
        )
        dist = result.scalar_one_or_none()
        if not dist:
            dist = Distributor(
                tenant_id=tenant_id,
                name=dist_data["name"],
                code=dist_data["code"],
                contact_name=dist_data["contact"],
                status="active",
            )
            db.add(dist)
            await db.flush()
            await db.refresh(dist)
        distributors.append(dist)

    # 区域
    regions = []
    for reg_data in REGIONS_DATA:
        result = await db.execute(select(Region).where(Region.tenant_id == tenant_id, Region.code == reg_data["code"]))
        reg = result.scalar_one_or_none()
        dist = distributors[reg_data["dist"]]
        if not reg:
            reg = Region(
                tenant_id=tenant_id,
                name=reg_data["name"],
                code=reg_data["code"],
                province=reg_data["province"],
                city=reg_data["city"],
                coverage_type="city",
                coverage_areas=[{"province": reg_data["province"], "city": reg_data["city"]}],
                distributor_id=dist.id,
                status="active",
            )
            db.add(reg)
            await db.flush()
            await db.refresh(reg)
        else:
            reg.distributor_id = dist.id
            reg.status = "active"
            reg.coverage_type = "city"
            reg.coverage_areas = [{"province": reg_data["province"], "city": reg_data["city"]}]
            await db.flush()
        regions.append(reg)

    # 门店
    stores = []
    for store_data in STORES_DATA:
        result = await db.execute(select(Store).where(Store.tenant_id == tenant_id, Store.code == store_data["code"]))
        store = result.scalar_one_or_none()
        region = regions[store_data["region"]]
        dist = distributors[REGIONS_DATA[store_data["region"]]["dist"]]
        if not store:
            store = Store(
                tenant_id=tenant_id,
                name=store_data["name"],
                code=store_data["code"],
                region_id=region.id,
                distributor_id=dist.id,
                address=store_data["address"],
                status="active",
            )
            db.add(store)
            await db.flush()
            await db.refresh(store)
        else:
            store.region_id = region.id
            store.distributor_id = dist.id
            store.status = "active"
            await db.flush()
        stores.append(store)

    # 码分配：将激活码分配给门店
    activated_items = [i for i in code_items if i.status == CodeItemStatus.activated]
    if activated_items and stores:
        # 获取所有码批次
        batch_ids = list({item.code_batch_id for item in activated_items})
        for batch_idx, batch_id in enumerate(batch_ids):
            batch_items = [i for i in activated_items if i.code_batch_id == batch_id]
            if not batch_items:
                continue
            primary_region = regions[batch_idx % len(regions)]
            primary_dist = distributors[REGIONS_DATA[batch_idx % len(regions)]["dist"]]
            result = await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id))
            code_batch = result.scalar_one_or_none()
            if code_batch:
                code_batch.distributor_id = primary_dist.id
                code_batch.region_id = primary_region.id

            per_store = max(1, len(batch_items) // len(stores))
            now_str = utcnow().replace(microsecond=0).isoformat()
            for idx, store in enumerate(stores):
                region = regions[STORES_DATA[idx]["region"]] if idx < len(STORES_DATA) else regions[0]
                dist = (
                    distributors[REGIONS_DATA[STORES_DATA[idx]["region"]]["dist"]]
                    if idx < len(STORES_DATA)
                    else distributors[0]
                )
                alloc_qty = min(per_store, len(batch_items) - idx * per_store)
                if alloc_qty <= 0:
                    break
                # 检查是否已有分配
                result = await db.execute(
                    select(CodeAllocation).where(
                        CodeAllocation.tenant_id == tenant_id,
                        CodeAllocation.batch_id == batch_id,
                        CodeAllocation.store_id == store.id,
                    )
                )
                alloc = result.scalar_one_or_none()
                if alloc:
                    alloc.quantity = alloc_qty
                    alloc.distributor_id = dist.id
                    alloc.region_id = region.id
                else:
                    db.add(
                        CodeAllocation(
                            tenant_id=tenant_id,
                            batch_id=batch_id,
                            store_id=store.id,
                            region_id=region.id,
                            distributor_id=dist.id,
                            quantity=alloc_qty,
                            allocated_at=now_str,
                        )
                    )
        await db.flush()

    # 账号渠道作用域
    dist_account = next((a for a in accounts if a.email == "dist@demo.com"), None)
    store_account = next((a for a in accounts if a.email == "store@demo.com"), None)
    if dist_account and distributors:
        await create_account_scope(db, tenant_id, dist_account.id, "distributor", distributor_id=distributors[0].id)
    if store_account and stores:
        await create_account_scope(db, tenant_id, store_account.id, "store", store_id=stores[0].id)

    return {"distributors": distributors, "regions": regions, "stores": stores}


# ═══════════════════════════════════════════════════════
# 阶段 5: 扫码事件
# ═══════════════════════════════════════════════════════


async def _ensure_scan_events(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    code_items: list[CodeItem],
) -> int:
    """批量生成 60 天的扫码事件，返回总条数"""
    # 检查是否已有扫码事件
    result = await db.execute(select(func.count()).select_from(ScanEvent).where(ScanEvent.tenant_id == tenant_id))
    existing_count = result.scalar_one()
    if existing_count > 1000:
        return existing_count  # 已有足够数据，跳过

    activated_items = [item for item in code_items if item.status == CodeItemStatus.activated]
    if not activated_items:
        return 0

    public_ids = [item.public_id for item in activated_items]
    # 扩展 public_id 池：每个码可以被扫多次
    total_events = 0
    batch_size = 1000

    events_batch: list[ScanEvent] = []
    for day_idx in range(TOTAL_DAYS):
        day_count = _scan_count_for_day(day_idx)
        for _ in range(day_count):
            public_id = random.choice(public_ids)
            env, ua = _pick_env()
            scan_time = _random_scan_time(day_idx)
            ip_hash = hashlib.sha256(f"demo-ip-{random.randint(1, 5000)}".encode()).hexdigest()[:32]
            is_first = random.random() < 0.70  # 70% 首扫

            events_batch.append(
                ScanEvent(
                    tenant_id=tenant_id,
                    public_id=public_id,
                    scan_time=scan_time,
                    ip_hash=ip_hash,
                    user_agent=ua,
                    is_first_scan=is_first,
                    environment=env,
                )
            )
            total_events += 1

            if len(events_batch) >= batch_size:
                db.add_all(events_batch)
                await db.flush()
                # 从 identity map 中移除，避免后续操作变慢
                for e in events_batch:
                    db.expunge(e)
                events_batch = []

    if events_batch:
        db.add_all(events_batch)
        await db.flush()
        for e in events_batch:
            db.expunge(e)

    return total_events


# ═══════════════════════════════════════════════════════
# 阶段 6: 活动与权益
# ═══════════════════════════════════════════════════════

CAMPAIGNS_DATA = [
    {
        "name": "首扫领券加企微复购活动",
        "campaign_type": "coupon",
        "status": CampaignStatus.ACTIVE,
        "start_offset": -3,
        "end_offset": 30,
        "rules": {"first_scan_only": True, "per_person_limit": 1, "channels": ["wechat", "h5"]},
        "description": "首扫领券，引导加企微和商城复购。",
        "benefit_name": "20 元复购券",
        "benefit_type": "external_link",
        "benefit_config": {"url": "https://shop.example.com/demo-rice", "amount": 20, "threshold": 99},
        "stock_total": 1000,
        "stock_used": 36,
    },
    {
        "name": "五常大米限时折扣",
        "campaign_type": "discount",
        "status": CampaignStatus.ACTIVE,
        "start_offset": -7,
        "end_offset": 7,
        "rules": {"per_person_limit": 2, "discount_rate": 0.85},
        "description": "限时 85 折促销活动。",
        "benefit_name": "85折优惠券",
        "benefit_type": "platform_coupon",
        "benefit_config": {"discount_rate": 0.85, "min_purchase": 50},
        "stock_total": 500,
        "stock_used": 128,
    },
    {
        "name": "新品蜂蜜体验装试用",
        "campaign_type": "trial",
        "status": CampaignStatus.ACTIVE,
        "start_offset": -1,
        "end_offset": 15,
        "rules": {"first_scan_only": True, "per_person_limit": 1},
        "description": "蜂蜜新品扫码免费领取体验装。",
        "benefit_name": "蜂蜜体验装 30g",
        "benefit_type": "form_benefit",
        "benefit_config": {"form_fields": ["name", "address", "phone"], "shipping_required": True},
        "stock_total": 300,
        "stock_used": 45,
    },
    {
        "name": "端午礼盒预售",
        "campaign_type": "presale",
        "status": CampaignStatus.DRAFT,
        "start_offset": 5,
        "end_offset": 20,
        "rules": {"per_person_limit": 3, "deposit_required": True},
        "description": "端午有机杂粮礼盒预售，定金翻倍。",
        "benefit_name": "端午礼盒预售定金翻倍",
        "benefit_type": "external_link",
        "benefit_config": {"url": "https://shop.example.com/presale-duanwu", "deposit": 50, "value": 100},
        "stock_total": 200,
        "stock_used": 0,
    },
    {
        "name": "春茶品鉴会",
        "campaign_type": "event",
        "status": CampaignStatus.ENDED,
        "start_offset": -45,
        "end_offset": -30,
        "rules": {"per_person_limit": 1, "registration_required": True},
        "description": "春茶品鉴会线上报名，扫码预约。",
        "benefit_name": "品鉴会入场券",
        "benefit_type": "private_domain",
        "benefit_config": {"wechat_group": "spring-tea-tasting", "qr_code_url": "https://example.com/qr"},
        "stock_total": 100,
        "stock_used": 87,
    },
]


async def _ensure_campaigns(db: AsyncSession, tenant_id: uuid.UUID, consumer_ids: list[str]) -> list[Campaign]:
    """创建活动与权益，含领取记录"""
    now = utcnow()
    campaigns = []

    for camp_data in CAMPAIGNS_DATA:
        result = await db.execute(
            select(Campaign).where(Campaign.tenant_id == tenant_id, Campaign.name == camp_data["name"])
        )
        campaign = result.scalar_one_or_none()
        if not campaign:
            campaign = Campaign(
                tenant_id=tenant_id,
                name=camp_data["name"],
                campaign_type=camp_data["campaign_type"],
                status=camp_data["status"],
                start_at=(now + timedelta(days=camp_data["start_offset"])).strftime("%Y-%m-%dT%H:%M:%SZ"),
                end_at=(now + timedelta(days=camp_data["end_offset"])).strftime("%Y-%m-%dT%H:%M:%SZ"),
                rules_json=camp_data["rules"],
                description=camp_data["description"],
            )
            db.add(campaign)
            await db.flush()
            await db.refresh(campaign)

        # 权益
        result = await db.execute(
            select(Benefit).where(Benefit.tenant_id == tenant_id, Benefit.campaign_id == campaign.id)
        )
        benefit = result.scalar_one_or_none()
        if benefit:
            benefit.stock_total = camp_data["stock_total"]
            benefit.stock_used = max(benefit.stock_used, camp_data["stock_used"])
        else:
            benefit = Benefit(
                tenant_id=tenant_id,
                campaign_id=campaign.id,
                name=camp_data["benefit_name"],
                benefit_type=camp_data["benefit_type"],
                config_json=camp_data["benefit_config"],
                stock_total=camp_data["stock_total"],
                stock_used=camp_data["stock_used"],
                per_person_limit=1,
                status="active",
            )
            db.add(benefit)
            await db.flush()
            await db.refresh(benefit)

        # 为已领取的权益创建 BenefitClaim 记录
        if camp_data["stock_used"] > 0 and consumer_ids:
            existing_claims = (
                await db.execute(
                    select(func.count())
                    .select_from(BenefitClaim)
                    .where(
                        BenefitClaim.tenant_id == tenant_id,
                        BenefitClaim.benefit_id == benefit.id,
                    )
                )
            ).scalar_one()
            claims_to_create = max(0, camp_data["stock_used"] - existing_claims)
            if claims_to_create > 0:
                for i in range(claims_to_create):
                    consumer_id = random.choice(consumer_ids)
                    db.add(
                        BenefitClaim(
                            tenant_id=tenant_id,
                            benefit_id=benefit.id,
                            campaign_id=campaign.id,
                            consumer_id=consumer_id,
                            idempotency_key=f"demo-claim-{benefit.id}-{i}",
                            claim_type="claim",
                            status="success",
                            delivery_status="not_required",
                        )
                    )
                await db.flush()

        campaigns.append(campaign)

    return campaigns


# ═══════════════════════════════════════════════════════
# 阶段 7: 消费者与积分
# ═══════════════════════════════════════════════════════

MEMBER_LEVEL_WEIGHTS = [
    ("normal", 0.50, (0, 200)),
    ("silver", 0.25, (200, 800)),
    ("gold", 0.18, (800, 2000)),
    ("platinum", 0.07, (2000, 5000)),
]


def _assign_member_level() -> tuple[str, int]:
    """按权重分配会员等级和对应积分"""
    r = random.random()
    cumulative = 0.0
    for level, weight, (min_pts, max_pts) in MEMBER_LEVEL_WEIGHTS:
        cumulative += weight
        if r <= cumulative:
            return level, random.randint(min_pts, max_pts)
    return "normal", random.randint(0, 200)


async def _ensure_consumers(db: AsyncSession, tenant_id: uuid.UUID) -> tuple[list[ConsumerProfile], list[str]]:
    """创建消费者档案并发放积分，返回消费者列表和 consumer_id 字符串列表"""
    target_count = 200

    result = await db.execute(
        select(func.count()).select_from(ConsumerProfile).where(ConsumerProfile.tenant_id == tenant_id)
    )
    existing = result.scalar_one()
    if existing >= target_count:
        result = await db.execute(
            select(ConsumerProfile).where(ConsumerProfile.tenant_id == tenant_id).limit(target_count)
        )
        consumers = list(result.scalars().all())
        consumer_ids = [str(c.id) for c in consumers]
        return consumers, consumer_ids

    consumers = []
    for i in range(target_count):
        nickname = random.choice(NICKNAME_POOL)

        level, points = _assign_member_level()
        consumer = ConsumerProfile(
            tenant_id=tenant_id,
            nickname=f"{nickname}{random.randint(1, 999) if random.random() < 0.3 else ''}",
            member_level=level,
            total_points=points,
            wechat_openid=f"demo_o{hashlib.md5(f'consumer-{i}'.encode()).hexdigest()[:24]}"
            if random.random() < 0.6
            else None,
            tags=random.choice(["扫码用户", "会员", "高活跃", None]),
            extra_data={
                "source": "demo",
                "city": random.choice(["上海", "杭州", "南京", "苏州", "广州", "深圳", "北京", "成都"]),
            },
        )
        db.add(consumer)
        consumers.append(consumer)

    # 批量 flush
    await db.flush()
    # 批量 refresh（一次查询获取所有 ID）
    consumer_ids_db = [c.id for c in consumers]
    result = await db.execute(select(ConsumerProfile).where(ConsumerProfile.id.in_(consumer_ids_db)))
    consumers = list(result.scalars().all())

    # 为有积分的消费者创建积分流水
    for consumer in consumers:
        if consumer.total_points > 0:
            # 模拟扫码获得的积分流水
            txn = PointTransaction(
                tenant_id=tenant_id,
                consumer_id=consumer.id,
                amount=consumer.total_points,
                balance_after=consumer.total_points,
                txn_type=PointTransactionType.earning,
                reason="扫码积分奖励（演示数据）",
                reference_id=f"demo-scan-{random.randint(1, 10000)}",
            )
            db.add(txn)
    await db.flush()

    # 创建积分规则
    result = await db.execute(select(func.count()).select_from(PointRule).where(PointRule.tenant_id == tenant_id))
    if result.scalar_one() == 0:
        for rule_type, points, desc in [
            ("scan", 10, "每次扫码获得 10 积分"),
            ("first_scan", 50, "首扫额外奖励 50 积分"),
            ("daily_checkin", 5, "每日签到获得 5 积分"),
        ]:
            db.add(
                PointRule(
                    tenant_id=tenant_id,
                    rule_type=rule_type,
                    points=points,
                    enabled=True,
                    daily_limit=3 if rule_type == "scan" else 1,
                    description=desc,
                )
            )
        await db.flush()

    # 创建积分商城商品
    result = await db.execute(select(func.count()).select_from(PointProduct).where(PointProduct.tenant_id == tenant_id))
    if result.scalar_one() == 0:
        point_products_data = [
            ("满减券 10 元", 100, 500, "全场满 99 减 10"),
            ("品牌定制帆布袋", 500, 50, "青岭良仓定制帆布袋"),
            ("产地体验游抽奖券", 1000, 20, "抽取产地一日游名额"),
            ("VIP 专属客服月卡", 2000, 100, "一个月专属客服通道"),
            ("新品试吃礼盒", 3000, 30, "当季新品试吃装"),
        ]
        for name, cost, stock, desc in point_products_data:
            db.add(
                PointProduct(
                    tenant_id=tenant_id,
                    name=name,
                    description=desc,
                    points_cost=cost,
                    stock=stock,
                    enabled=True,
                )
            )
        await db.flush()

    consumer_ids = [str(c.id) for c in consumers]
    return consumers, consumer_ids


# ═══════════════════════════════════════════════════════
# 阶段 8: 风控数据
# ═══════════════════════════════════════════════════════

DIVERSION_CLUES_DATA = [
    {"expected": "上海", "detected": "北京", "resolved": False},
    {"expected": "杭州", "detected": "武汉", "resolved": True},
    {"expected": "广州", "detected": "长沙", "resolved": False},
    {"expected": "南京", "detected": "郑州", "resolved": True},
    {"expected": "苏州", "detected": "合肥", "resolved": False},
]


async def _ensure_risk_data(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    code_items: list[CodeItem],
    channels: dict,
) -> None:
    """创建窜货线索、风险告警、风险规则和拦截记录"""
    activated_items = [i for i in code_items if i.status == CodeItemStatus.activated]
    if not activated_items:
        return

    distributors = channels["distributors"]
    regions = channels["regions"]

    # 窜货线索
    for idx, clue_data in enumerate(DIVERSION_CLUES_DATA):
        if idx >= len(activated_items):
            break
        code_item = activated_items[idx]
        dist_idx = min(idx, len(distributors) - 1)
        reg_idx = min(idx, len(regions) - 1)
        diversion_code = f"DEMO-DIVERSION-{idx + 1}"

        result = await db.execute(
            select(DiversionClue).where(
                DiversionClue.tenant_id == tenant_id,
                DiversionClue.public_id == diversion_code,
            )
        )
        clue = result.scalar_one_or_none()
        if not clue:
            clue = DiversionClue(
                tenant_id=tenant_id,
                public_id=diversion_code,
                code_item_id=code_item.id,
                expected_region=clue_data["expected"],
                detected_city=clue_data["detected"],
                distributor_id=distributors[dist_idx].id,
                region_id=regions[reg_idx].id,
                ip_hash=f"demo-diversion-ip-{idx}",
                resolved=clue_data["resolved"],
            )
            if clue_data["resolved"]:
                clue.resolution_action = "confirmed"
                clue.resolution_note = "已联系经销商核实，确认正常调拨。"
                clue.resolved_at = utcnow() - timedelta(days=random.randint(1, 10))
            db.add(clue)

    # 风险规则
    risk_rules_data = [
        {
            "name": "异地扫码预警",
            "rule_type": "multi_location",
            "action": "warn",
            "config": {"max_locations": 3, "time_window_hours": 24},
        },
        {
            "name": "疑似仿制码拦截",
            "rule_type": "suspected_copy",
            "action": "block",
            "config": {"min_scan_interval_seconds": 5},
        },
        {
            "name": "高风险码冻结",
            "rule_type": "risk_frozen",
            "action": "block",
            "config": {"max_daily_scans_per_code": 50},
        },
    ]
    risk_rules = []
    for rule_data in risk_rules_data:
        result = await db.execute(
            select(RiskRule).where(
                RiskRule.tenant_id == tenant_id,
                RiskRule.rule_type == rule_data["rule_type"],
            )
        )
        rule = result.scalar_one_or_none()
        if not rule:
            rule = RiskRule(
                tenant_id=tenant_id,
                name=rule_data["name"],
                rule_type=rule_data["rule_type"],
                action=rule_data["action"],
                config=rule_data["config"],
                enabled=True,
            )
            db.add(rule)
            await db.flush()
            await db.refresh(rule)
        risk_rules.append(rule)

    # 风险告警
    alert_types = [
        (RiskAlertType.multi_location, "同一码在多个城市被扫描"),
        (RiskAlertType.suspected_copy, "疑似仿制码，短时间大量扫码"),
        (RiskAlertType.risk_frozen, "码已被系统自动冻结"),
    ]
    existing_alerts = (
        await db.execute(select(func.count()).select_from(RiskAlert).where(RiskAlert.tenant_id == tenant_id))
    ).scalar_one()

    if existing_alerts < 5:
        for i in range(8 - existing_alerts):
            if i >= len(activated_items):
                break
            code_item = activated_items[i]
            alert_type, detail = alert_types[i % len(alert_types)]
            db.add(
                RiskAlert(
                    tenant_id=tenant_id,
                    alert_type=alert_type,
                    public_id=code_item.public_id,
                    code_item_id=code_item.id,
                    detail=detail,
                    ip_hash=f"demo-alert-ip-{i}",
                    resolved=random.random() < 0.4,
                )
            )

    # 拦截记录
    existing_interceptions = (
        await db.execute(
            select(func.count()).select_from(InterceptionRecord).where(InterceptionRecord.tenant_id == tenant_id)
        )
    ).scalar_one()

    if existing_interceptions < 2 and risk_rules:
        for i in range(3):
            rule = risk_rules[i % len(risk_rules)]
            db.add(
                InterceptionRecord(
                    tenant_id=tenant_id,
                    risk_rule_id=rule.id,
                    action=rule.action,
                    context={"reason": "演示数据", "ip": f"192.168.{i}.{random.randint(1, 254)}"},
                    consumer_id=f"demo-consumer-{i}",
                    auto_triggered=random.random() < 0.7,
                    action_taken="blocked" if rule.action == "block" else "warned",
                )
            )

    await db.flush()


# ═══════════════════════════════════════════════════════
# 阶段 9: 页面模板
# ═══════════════════════════════════════════════════════

PAGE_TEMPLATES_DATA = [
    # (name, template_type, product_name, config_json_factory)
    {
        "name": "五常稻花香扫码信任页",
        "template_type": "traceability",
        "product_name": "五常稻花香大米 5kg",
        "config": lambda p: {
            "brand_name": "青岭良仓",
            "product_name": p,
            "story_content": "来自黑龙江五常核心产区，批次检测合格，扫码领取首购福利。",
            "traceability": {
                "origin": "黑龙江省哈尔滨市五常市",
                "production_date": str(date.today() - timedelta(days=20)),
                "expiry_date": str(date.today() + timedelta(days=345)),
            },
            "benefit": "首扫领取 20 元复购券",
            "private_domain": "企业微信客服 / 小程序商城",
        },
    },
    {
        "name": "蜂蜜新品营销页",
        "template_type": "product_info",
        "product_name": "长白山椴树蜂蜜 500g",
        "config": lambda p: {
            "brand_name": "青岭良仓",
            "product_name": p,
            "hero_image": "https://placehold.co/400x300/FFD700/333?text=蜂蜜",
            "highlights": ["长白山原始林区", "天然椴树蜜源", "扫码领试用装"],
            "benefit": "扫码免费领取 30g 体验装",
            "cta_text": "立即领取",
        },
    },
    {
        "name": "青岭良仓品牌故事页",
        "template_type": "brand_story",
        "product_name": None,  # 不关联具体产品
        "config": lambda p: {
            "brand_name": "青岭良仓",
            "story_title": "从田野到餐桌的信任",
            "story_content": "青岭良仓创立于 2023 年，致力于为消费者提供产地透明、品质可追溯的优质农产品。",
            "values": ["产地溯源", "品质检测", "扫码领福利", "私域复购"],
            "contact": "客服热线 400-888-0000",
        },
    },
    {
        "name": "龙井明前茶溯源页",
        "template_type": "traceability",
        "product_name": "西湖龙井明前茶 100g",
        "config": lambda p: {
            "brand_name": "茶语清风",
            "product_name": p,
            "story_content": "西湖龙井明前一级，来自核心产区，扫码查看茶园视频。",
            "traceability": {
                "origin": "浙江省杭州市西湖区",
                "production_date": str(date.today() - timedelta(days=30)),
                "expiry_date": str(date.today() + timedelta(days=180)),
            },
            "benefit": "扫码查看茶园实时视频",
        },
    },
    {
        "name": "赤霞珠干红品鉴页",
        "template_type": "product_info",
        "product_name": "赤霞珠干红 750ml",
        "config": lambda p: {
            "brand_name": "醉美庄园",
            "product_name": p,
            "hero_image": "https://placehold.co/400x300/8B0000/FFF?text=干红",
            "highlights": ["贺兰山东麓产区", "橡木桶陈酿 12 个月", "扫码品鉴笔记"],
            "tasting_notes": {"color": "深宝石红", "aroma": "黑莓、黑加仑、香草", "palate": "单宁柔顺，余味悠长"},
            "cta_text": "查看品鉴笔记",
        },
    },
]


async def _ensure_page_templates(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    brand_records: list[dict],
    created_by: uuid.UUID,
) -> list[PageTemplate]:
    """创建页面模板和已发布版本"""
    templates = []
    # 构建 product_id 查找表
    product_id_map: dict[str, uuid.UUID] = {}
    for brand_rec in brand_records:
        for prod_rec in brand_rec["products"]:
            product_id_map[prod_rec["product"].name] = prod_rec["product"].id

    for tmpl_data in PAGE_TEMPLATES_DATA:
        product_id = product_id_map.get(tmpl_data["product_name"]) if tmpl_data["product_name"] else None

        result = await db.execute(
            select(PageTemplate).where(
                PageTemplate.tenant_id == tenant_id,
                PageTemplate.name == tmpl_data["name"],
            )
        )
        template = result.scalar_one_or_none()
        if not template:
            template = PageTemplate(
                tenant_id=tenant_id,
                product_id=product_id,
                name=tmpl_data["name"],
                template_type=tmpl_data["template_type"],
                status=PageTemplateStatus.active,
                description=f"演示用{tmpl_data['template_type']}页面模板",
            )
            db.add(template)
            await db.flush()
            await db.refresh(template)

        # 确保有已发布版本
        result = await db.execute(
            select(PageVersion).where(
                PageVersion.tenant_id == tenant_id,
                PageVersion.page_template_id == template.id,
                PageVersion.status == PageVersionStatus.published,
            )
        )
        version = result.scalar_one_or_none()
        config = tmpl_data["config"](tmpl_data["product_name"] or "")
        if version:
            version.config_json = config
        else:
            db.add(
                PageVersion(
                    tenant_id=tenant_id,
                    page_template_id=template.id,
                    version=1,
                    config_json=config,
                    status=PageVersionStatus.published,
                    created_by=created_by,
                )
            )
        await db.flush()
        templates.append(template)

    return templates


# ═══════════════════════════════════════════════════════
# 阶段 10: 统计聚合
# ═══════════════════════════════════════════════════════


async def _aggregate_stats(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """批量聚合 60 天的 DailyScanStats"""
    days_done = 0
    for offset in range(TOTAL_DAYS):
        target_date = date.today() - timedelta(days=offset)
        await aggregate_daily_stats(db, tenant_id, target_date)
        days_done += 1
        # 每 10 天 flush 一次，避免事务过大
        if days_done % 10 == 0:
            await db.flush()
    await db.flush()
    return days_done


# ═══════════════════════════════════════════════════════
# 清理功能
# ═══════════════════════════════════════════════════════


async def _clean_demo_data(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """删除所有演示数据（保留租户本身）"""
    typer.echo("\U0001f9f9 正在清理演示数据...")
    # 按依赖顺序删除
    for model in [
        InterceptionRecord,
        RiskAlert,
        RiskRule,
        RiskNotification,
        PointRedemption,
        PointTransaction,
        PointProduct,
        PointRule,
        BenefitClaim,
        Benefit,
        Campaign,
        ConsumerProfile,
        DiversionClue,
        CodeAllocation,
        AccountChannelScope,
        ScanEvent,
        PageVersion,
        PageTemplate,
        CodeItem,
        CodeBatch,
        ProductionBatch,
        SKU,
        Product,
        Brand,
        Store,
        Region,
        Distributor,
    ]:
        result = await db.execute(delete(model).where(model.tenant_id == tenant_id))
        if result.rowcount:
            typer.echo(f"  删除 {model.__tablename__}: {result.rowcount} 条")
    await db.flush()
    typer.echo("✅ 清理完成")


# ═══════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════


@app.command()
def generate():
    """一键生成全部演示数据"""
    typer.echo("\U0001f3ad 一码通演示数据生成器")
    typer.echo("=" * 50)

    async def _run():
        start = time.time()

        # ── 阶段 A：基础数据（租户/品牌/码/渠道/扫码）──
        async with async_session() as db:
            p = Progress(10)

            # 1. 租户与账号
            tenant = await _ensure_tenant(db)
            org = await _ensure_org(db, tenant.id)
            accounts = await _ensure_accounts(db, tenant.id, org.id)
            admin_account = next((a for a in accounts if a.email == "admin@demo.com"), accounts[0])
            tenant_id = tenant.id
            admin_id = admin_account.id
            p.step("租户与账号", f"({len(accounts)} 个账号)")

            # 2. 品牌与产品
            brand_records = await _ensure_brands_products(db, tenant_id)
            brand_count = len(brand_records)
            prod_count = sum(len(b["products"]) for b in brand_records)
            sku_count = sum(len(p_rec["skus"]) for b in brand_records for p_rec in b["products"])
            p.step("品牌与产品", f"({brand_count}品牌/{prod_count}产品/{sku_count}SKU)")

            # 3. 码批次
            code_items = await _ensure_code_batches(db, tenant_id, admin_id, brand_records)
            activated = len([i for i in code_items if i.status == CodeItemStatus.activated])
            p.step("码批次", f"({len(code_items)} 码, {activated} 激活)")
            # 保存 public_id 列表供后续使用
            active_public_id = next((i.public_id for i in code_items if i.status == CodeItemStatus.activated), None)

            # 4. 渠道
            channels = await _ensure_channels(db, tenant_id, accounts, code_items)
            p.step(
                "渠道体系",
                f"({len(channels['distributors'])}经销商/{len(channels['regions'])}区域/{len(channels['stores'])}门店)",
            )

            # 5. 扫码事件
            event_count = await _ensure_scan_events(db, tenant_id, code_items)
            p.step("扫码事件", f"({event_count:,} 次)")

            # --- Demo Agency Tenant ---
            agency_slug = "demo-agency"
            result = await db.execute(select(Tenant).where(Tenant.slug == agency_slug))
            existing_agency = result.scalar_one_or_none()
            if not existing_agency:
                agency_tenant = Tenant(
                    name="示例代运营服务商",
                    slug=agency_slug,
                    tenant_type="agency",
                    plan="pro",
                )
                db.add(agency_tenant)
                await db.flush()

                agency_org = Organization(
                    tenant_id=agency_tenant.id,
                    name="示例代运营服务商",
                )
                db.add(agency_org)
                await db.flush()

                agency_admin = Account(
                    tenant_id=agency_tenant.id,
                    organization_id=agency_org.id,
                    email="agency_admin@demo.com",
                    hashed_password=hash_password("demopass"),
                    name="代运营管理员",
                )
                db.add(agency_admin)
                await db.flush()

                auth = AgencyAuthorization(
                    agency_tenant_id=agency_tenant.id,
                    client_tenant_id=tenant.id,
                    scope=["pages", "campaigns", "analytics", "products", "codes"],
                    status=AgencyAuthStatus.active,
                    granted_by=None,
                )
                db.add(auth)
                await db.flush()
                typer.echo(f"  Created demo agency tenant: {agency_slug}")

            # 提交阶段 A，关闭 session 释放 identity map
            await db.commit()

        # ── 阶段 B：业务数据（消费者/活动/风控/页面/统计）── 新 session，干净的 identity map
        async with async_session() as db:
            # 6. 消费者
            consumers, consumer_ids = await _ensure_consumers(db, tenant_id)
            p.step("消费者与积分", f"({len(consumers)} 人)")

            # 7. 活动与权益
            campaigns = await _ensure_campaigns(db, tenant_id, consumer_ids)
            p.step("活动与权益", f"({len(campaigns)} 活动)")

            # 8. 风控（需要重新查询 code_items）
            code_items_b = list(
                (await db.execute(select(CodeItem).where(CodeItem.tenant_id == tenant_id).limit(100))).scalars().all()
            )
            await _ensure_risk_data(db, tenant_id, code_items_b, channels)
            p.step("风控数据", "(告警/窜货/拦截)")

            # 9. 页面模板
            await _ensure_page_templates(db, tenant_id, brand_records, admin_id)
            p.step("页面模板", "(5 模板)")

            # 10. 统计聚合
            days = await _aggregate_stats(db, tenant_id)
            p.step("统计聚合", f"({days} 天)")

            await db.commit()

        elapsed = time.time() - start
        typer.echo("=" * 50)
        typer.echo(f"✅ 完成！耗时 {elapsed:.1f}s\n")

        typer.echo("演示账号:")
        for account in DEMO_ACCOUNTS:
            typer.echo(f"  {account['title']}: {account['email']} / {account['password']} ({account['role']})")

        if active_public_id:
            typer.echo(f"\n示例扫码 URL: /c/{active_public_id}")

    asyncio.run(_run())


@app.command()
def clean():
    """清理所有演示数据"""

    async def _run():
        async with async_session() as db:
            result = await db.execute(select(Tenant).where(Tenant.slug == TENANT_SLUG))
            tenant = result.scalar_one_or_none()
            if not tenant:
                typer.echo(f"未找到演示租户 '{TENANT_SLUG}'")
                return
            await _clean_demo_data(db, tenant.id)
            await db.commit()

    asyncio.run(_run())


@app.command()
def reset():
    """清理后重新生成"""

    async def _run():
        async with async_session() as db:
            result = await db.execute(select(Tenant).where(Tenant.slug == TENANT_SLUG))
            tenant = result.scalar_one_or_none()
            if tenant:
                await _clean_demo_data(db, tenant.id)
                await db.commit()

    asyncio.run(_run())
    # 重新生成
    generate()

    # 保持 typer 正常退出


if __name__ == "__main__":
    app()
