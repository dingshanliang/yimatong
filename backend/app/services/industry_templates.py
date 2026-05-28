"""行业模板预置数据"""

FOOD_TRACEABILITY_TEMPLATE = {
    "name": "食品溯源页",
    "template_type": "traceability",
    "description": "食品行业标准模板：产品卡 + 溯源时间线 + 检测报告 + 企业资质",
    "config_json": {
        "dsl_version": "1.0",
        "title": "食品安全溯源",
        "theme": {"primary_color": "#1677ff", "background": "#ffffff"},
        "modules": [
            {"type": "product_card", "visible": True, "order": 1},
            {"type": "traceability_timeline", "visible": True, "order": 2},
            {"type": "inspection_report", "visible": True, "order": 3},
            {"type": "company_credentials", "visible": True, "order": 4},
            {"type": "activity_zone", "visible": False, "order": 5},
        ],
    },
}

AGRICULTURE_TEMPLATE = {
    "name": "农产品溯源页",
    "template_type": "traceability",
    "description": "农产品行业模板：强调产地信息、种植/采摘流程、质量认证",
    "config_json": {
        "dsl_version": "1.0",
        "title": "农产品溯源",
        "theme": {"primary_color": "#52c41a", "background": "#f6ffed"},
        "modules": [
            {"type": "product_card", "visible": True, "order": 1},
            {"type": "origin_info", "visible": True, "order": 2},
            {"type": "planting_timeline", "visible": True, "order": 3},
            {"type": "quality_certification", "visible": True, "order": 4},
            {"type": "traceability_timeline", "visible": True, "order": 5},
        ],
    },
}

GIFT_BOX_TEMPLATE = {
    "name": "礼盒页",
    "template_type": "brand_story",
    "description": "礼盒行业模板：强调品牌故事、礼盒内容物、节日活动推广",
    "config_json": {
        "dsl_version": "1.0",
        "title": "精品礼盒",
        "theme": {"primary_color": "#cf1322", "background": "#fff1f0"},
        "modules": [
            {"type": "product_card", "visible": True, "order": 1},
            {"type": "brand_story", "visible": True, "order": 2},
            {"type": "gift_contents", "visible": True, "order": 3},
            {"type": "festival_promotion", "visible": True, "order": 4},
            {"type": "activity_zone", "visible": True, "order": 5},
        ],
    },
}

ALL_TEMPLATES = [FOOD_TRACEABILITY_TEMPLATE, AGRICULTURE_TEMPLATE, GIFT_BOX_TEMPLATE]
