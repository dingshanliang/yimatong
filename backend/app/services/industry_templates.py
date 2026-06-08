"""行业模板预置数据 — 使用前端模块化 DSL 格式"""

FOOD_TRACEABILITY_TEMPLATE = {
    "name": "食品溯源页",
    "template_type": "traceability",
    "description": "食品行业标准模板：产品卡 + 溯源时间线 + 检测报告 + 企业资质",
    "config_json": {
        "modules": [
            {
                "id": "food_hero",
                "type": "product_hero",
                "enabled": True,
                "config": {"show_verify_badge": True},
            },
            {
                "id": "food_trace",
                "type": "light_traceability",
                "enabled": True,
                "config": {"fields": ["origin", "production_date", "batch_no"]},
            },
            {
                "id": "food_reports",
                "type": "test_reports",
                "enabled": True,
                "config": {},
            },
            {
                "id": "food_certs",
                "type": "certificates",
                "enabled": True,
                "config": {},
            },
            {
                "id": "food_cta",
                "type": "cta_group",
                "enabled": False,
                "config": {},
            },
        ],
        "routing": {"default_page": True, "campaign_periods": [{"mode": "evergreen"}]},
    },
}

AGRICULTURE_TEMPLATE = {
    "name": "农产品溯源页",
    "template_type": "traceability",
    "description": "农产品行业模板：强调产地信息、种植/采摘流程、质量认证",
    "config_json": {
        "modules": [
            {
                "id": "agri_hero",
                "type": "product_hero",
                "enabled": True,
                "config": {"show_verify_badge": True},
            },
            {
                "id": "agri_trace",
                "type": "light_traceability",
                "enabled": True,
                "config": {"fields": ["origin", "production_date", "expiry_date", "batch_no"]},
            },
            {
                "id": "agri_media",
                "type": "media_section",
                "enabled": True,
                "config": {},
            },
            {
                "id": "agri_certs",
                "type": "certificates",
                "enabled": True,
                "config": {},
            },
            {
                "id": "agri_legal",
                "type": "legal_terms",
                "enabled": True,
                "config": {"show_privacy_policy": True},
            },
        ],
        "routing": {"default_page": True, "campaign_periods": [{"mode": "evergreen"}]},
    },
}

GIFT_BOX_TEMPLATE = {
    "name": "礼盒页",
    "template_type": "brand_story",
    "description": "礼盒行业模板：强调品牌故事、礼盒内容物、节日活动推广",
    "config_json": {
        "modules": [
            {
                "id": "gift_hero",
                "type": "product_hero",
                "enabled": True,
                "config": {"show_verify_badge": True},
            },
            {
                "id": "gift_media",
                "type": "media_section",
                "enabled": True,
                "config": {},
            },
            {
                "id": "gift_trace",
                "type": "light_traceability",
                "enabled": True,
                "config": {"fields": ["origin", "production_date", "batch_no"]},
            },
            {
                "id": "gift_benefit",
                "type": "benefit_card",
                "enabled": True,
                "config": {},
            },
            {
                "id": "gift_cta",
                "type": "cta_group",
                "enabled": True,
                "config": {},
            },
        ],
        "routing": {"default_page": True, "campaign_periods": [{"mode": "evergreen"}]},
    },
}

ALL_TEMPLATES = [FOOD_TRACEABILITY_TEMPLATE, AGRICULTURE_TEMPLATE, GIFT_BOX_TEMPLATE]
