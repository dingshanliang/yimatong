"""AI 资料识别与文案生成服务（模拟实现）

提供产品资料识别（文本/图片）、文案生成、页面结构建议等功能。
当前为规则匹配模拟实现，后续可替换为真实 LLM 调用。
"""

import re
from pathlib import PurePosixPath

# 产品名称识别映射（全局常量）
PRODUCT_NAMES: dict[str, str] = {
    "脐橙": "脐橙",
    "蜂蜜": "蜂蜜",
    "大米": "大米",
    "茶叶": "茶叶",
    "龙井": "龙井",
    "普洱": "普洱",
    "红酒": "红酒",
    "牛奶": "牛奶",
    "酸奶": "酸奶",
}

# 文件名到产品名称映射
IMAGE_NAME_MAP: dict[str, str] = {
    "orange": "脐橙",
    "honey": "蜂蜜",
    "rice": "大米",
    "tea": "茶叶",
    "milk": "牛奶",
    "wine": "红酒",
    "apple": "苹果",
    "juice": "果汁",
}

# 文件名到品类映射
IMAGE_CATEGORY_MAP: dict[str, str] = {
    "orange": "水果",
    "apple": "水果",
    "honey": "蜂蜜",
    "rice": "粮食",
    "tea": "茶叶",
    "milk": "乳制品",
    "wine": "酒类",
    "juice": "饮料",
}

# 品类到推荐模板类型映射
CATEGORY_TEMPLATE_MAP: dict[str, str] = {
    "水果": "traceability",
    "蔬菜": "traceability",
    "粮食": "traceability",
    "食用油": "traceability",
    "蜂蜜": "brand_story",
    "茶叶": "brand_story",
    "酒类": "brand_story",
    "乳制品": "product_info",
    "饮料": "product_info",
}

_TEMPLATE_TYPE_LABELS: dict[str, str] = {
    "traceability": "溯源页",
    "brand_story": "品牌故事",
    "product_info": "产品展示页",
}


def generate_copywriting(
    copy_type: str,
    product_name: str,
    keywords: list[str],
) -> dict:
    """生成文案（模拟）"""
    if copy_type == "brand_story":
        kw_str = "、".join(keywords) if keywords else "品质"
        content = (
            f"{product_name}，{kw_str}。源自大自然的馈赠，每一份都承载着匠心与诚意。从产地到餐桌，"
            "我们用心守护每一个环节，只为让您品尝到最纯正的美味。"
        )
        return {"content": content}
    elif copy_type == "selling_points":
        items = [f"{kw}：{product_name}的核心优势" for kw in keywords] if keywords else ["优质品质"]
        return {"content": {"items": items}}
    return {"content": ""}


def extract_product_fields(text: str) -> dict:
    """从文本提取产品字段（模拟规则匹配）"""
    fields: dict = {}

    for keyword, name in PRODUCT_NAMES.items():
        if keyword in text:
            fields["product_name"] = name
            break
    else:
        fields["product_name"] = "未知产品"

    # 简单规则提取产地
    for prefix in ["产地", "来自", "源自"]:
        if prefix in text:
            idx = text.index(prefix)
            segment = text[idx + len(prefix) : idx + len(prefix) + 10]
            for sep in ["，", "、", "。", "，", ","]:
                if sep in segment:
                    segment = segment[: segment.index(sep)]
                    break
            fields["origin"] = segment.strip()
            break

    if "origin" not in fields:
        fields["origin"] = "未知"

    # 提取重量
    weight_match = re.search(r"(\d+\.?\d*)\s*(kg|公斤|斤|g|克)", text)
    if weight_match:
        fields["weight"] = f"{weight_match.group(1)}{weight_match.group(2)}"

    # 提取保质期
    shelf_match = re.search(r"保质期[：:?\s]*(\d+)\s*个?\s*(天|月|年)", text)
    if shelf_match:
        fields["shelf_life"] = f"{shelf_match.group(1)}{shelf_match.group(2)}"

    return {"fields": fields}


def extract_product_from_image(
    filename: str,
    content_type: str,
    content: bytes,
) -> dict:
    """从图片识别产品信息（模拟实现）

    当前为模拟实现，根据文件名推断产品信息。
    生产环境可替换为 OCR / 计算机视觉 API 调用。
    """
    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    if content_type not in allowed_types:
        raise ValueError(
            f"Content type '{content_type}' is not supported. "
            f"Allowed: {', '.join(sorted(allowed_types))}"
        )

    fields: dict = {}

    # 从文件名推断产品名称
    stem = PurePosixPath(filename).stem.lower()
    for key, name in IMAGE_NAME_MAP.items():
        if key in stem:
            fields["product_name"] = name
            break
    else:
        fields["product_name"] = "待识别产品"

    # 根据文件名推断品类
    for key, cat in IMAGE_CATEGORY_MAP.items():
        if key in stem:
            fields["category"] = cat
            break
    else:
        fields["category"] = "其他"

    # 模拟识别结果
    fields["confidence"] = 0.85
    fields["source"] = "image_recognition"

    return {"fields": fields}


def generate_page_copy(
    product_name: str,
    category: str,
    keywords: list[str],
) -> dict:
    """生成页面文案和推荐模板

    综合调用文案生成和页面结构建议，返回完整方案。
    """
    template_type = CATEGORY_TEMPLATE_MAP.get(category, "product_info")

    # 生成文案
    brand_story = generate_copywriting("brand_story", product_name, keywords)
    selling_points = generate_copywriting("selling_points", product_name, keywords)

    # 生成页面结构建议
    page_suggestion = suggest_page_structure(product_name, category)

    template_label = _TEMPLATE_TYPE_LABELS.get(template_type, "产品展示页")

    return {
        "copywriting": {
            "brand_story": brand_story["content"],
            "selling_points": selling_points["content"],
        },
        "recommended_template": {
            "template_type": template_type,
            "name": f"{product_name}{template_label}",
        },
        "page_suggestion": page_suggestion,
    }


def suggest_page_structure(product_name: str, category: str) -> dict:
    """页面结构建议（模拟）"""
    base_modules = ["hero_banner", "product_info", "traceability"]

    if category in ["水果", "蔬菜"]:
        base_modules.extend(["origin_map", "seasonal_calendar"])
    elif category in ["粮食", "食用油"]:
        base_modules.extend(["nutrition_facts", "cooking_tips"])
    elif category in ["蜂蜜", "茶叶"]:
        base_modules.extend(["craftsmanship", "tasting_notes"])
    else:
        base_modules.append("reviews")

    return {"modules": base_modules}


def generate_campaign(
    product_name: str,
    goal: str,
    target_audience: str,
) -> dict:
    """生成活动方案（模拟）"""
    if goal == "promotion":
        name = f"{product_name}扫码有礼"
        description = (
            f"面向{target_audience}，扫描{product_name}包装二维码即可参与抽奖活动，赢取精美礼品。"
        )
        benefits = ["优惠券", "积分奖励", "限量周边"]
    elif goal == "retention":
        name = f"{product_name}复购回馈"
        description = f"针对{target_audience}，多次扫码累积积分可兑换{product_name}系列产品。"
        benefits = ["积分翻倍", "专属折扣", "新品试吃"]
    else:
        name = f"{product_name}品牌推广"
        description = f"为{target_audience}提供{product_name}的品牌体验活动。"
        benefits = ["品牌周边", "体验装", "会员权益"]

    return {
        "name": name,
        "description": description,
        "suggested_benefits": benefits,
    }
